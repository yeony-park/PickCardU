"""Cookie-owned conversation endpoints and bounded contextual RAG orchestration."""
import hashlib
import re
import secrets
import time
from uuid import UUID

from fastapi import Query, Request, Response
from pickcardu_rag.answering import RewriteOutput, completed_context
from pickcardu_rag.errors import LlmUnavailable, RagError

from .chat_store import ChatStoreError
from .chat_scope import card_references, recall_answer, reference_groups, resolve_scope
from .chat_models import ExecutionContext, TurnInputSnapshot
from .chat_personalization import clarification_context, missing_personalization_clarification, resolve_personalization

COOKIE_NAME = 'pickcardu_browser'
TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')


def browser_token(request):
    token = request.cookies.get(COOKIE_NAME, '')
    return token if TOKEN_PATTERN.fullmatch(token) else None


def refresh_cookie(response, token):
    response.set_cookie(COOKIE_NAME, token, max_age=90*24*60*60, httponly=True,
                        samesite='lax', secure=False, path='/')


def build_chat_context(turns: list[dict], question: str, *, include_insufficient: bool = False) -> list[dict[str, str]]:
    messages = []
    for turn in turns:
        answer = turn.get('answer') or {}
        if turn.get('state') != 'completed' or (answer.get('answer_status') != 'answered' and not include_insufficient):
            continue
        cards = {card['card_key']: card for card in answer.get('cards', [])}
        text = answer.get('answer', '')
        if answer.get('answer_status') == 'insufficient_evidence':
            text = '이전 질문은 근거 부족 또는 대상 확인 요청입니다. 조건이 없다고 확인한 것이 아닙니다.\n' + text
        for index, recommendation in enumerate(answer.get('recommendations', []), 1):
            card = cards.get(recommendation['card_key'], {})
            text += f"\n{index}. {card.get('card_name', recommendation['card_key'])} · {card.get('issuer', '')}: {recommendation['reason']}"
        messages.extend([{'role': 'user', 'content': turn['query']}, {'role': 'assistant', 'content': text}])
    return completed_context(messages, question, max_pairs=2, max_chars=5500)


def turn_messages(turn):
    common = {'turn_id': turn['id'], 'client_request_id': turn['client_request_id'], 'created_at': turn['created_at']}
    answer, error = turn['answer'], turn['error']
    messages = [
        {**common, 'id': turn['id'] + ':user', 'seq': turn['seq']*2-1, 'role': 'user',
         'content': turn['query'], 'status': 'completed'},
        {**common, 'id': turn['id'] + ':assistant', 'seq': turn['seq']*2, 'role': 'assistant',
         'content': answer['answer'] if answer else (error['message'] if error else ''),
         'status': turn['state'], 'answer': answer, 'error': error, 'rewrite_usage': turn['rewrite_usage']},
    ]
    if turn['state'] == 'failed':
        messages[1]['input_snapshot'] = TurnInputSnapshot.model_validate(turn['request']).model_dump(mode='json')
    return messages


def register_chat_routes(app, settings, store, provider, generate_answer, index_loader):
    from .chat_models import (
        BrowserSessionRequest, BrowserSessionResponse, ChatRequest, Conversation,
        ConversationPage, CreateConversationRequest, MessagesPage, TurnResponse,
    )
    from .models import ErrorResponse, QueryRequest

    errors = {status: {'model': ErrorResponse} for status in (401, 403, 404, 409, 422, 503)}

    def require_owner(request):
        if request.method in ('POST', 'DELETE'):
            origin = request.headers.get('origin')
            own_origin = str(request.base_url).rstrip('/')
            if origin not in (*settings.allowed_origins, own_origin):
                raise ChatStoreError(403, 'ORIGIN_NOT_ALLOWED', '허용되지 않은 요청 출처입니다.')
        token = browser_token(request)
        if token is None:
            raise ChatStoreError(401, 'BROWSER_SESSION_REQUIRED', '브라우저 대화 세션을 먼저 초기화해 주세요.')
        return hashlib.sha256(token.encode()).hexdigest()

    @app.post('/v1/browser-session', response_model=BrowserSessionResponse, responses=errors)
    def initialize_session(payload: BrowserSessionRequest, request: Request, response: Response):
        if request.headers.get('origin') not in (*settings.allowed_origins, str(request.base_url).rstrip('/')):
            raise ChatStoreError(403, 'ORIGIN_NOT_ALLOWED', '허용되지 않은 요청 출처입니다.')
        token = browser_token(request) or secrets.token_urlsafe(32)
        refresh_cookie(response, token)
        return {'status': 'ready'}

    @app.get('/v1/conversations', response_model=ConversationPage, responses=errors)
    def conversations(request: Request, limit: int = Query(40, ge=1, le=100), cursor: str | None = Query(None, max_length=1024)):
        return store.list_conversations(require_owner(request), limit=limit, cursor=cursor)

    @app.post('/v1/conversations', response_model=Conversation, status_code=201,
              responses={**errors, 200: {'model': Conversation}})
    def create_conversation(payload: CreateConversationRequest, request: Request, response: Response):
        result = store.create_conversation(require_owner(request), str(payload.client_conversation_id), payload.survey_context)
        response.status_code = 201 if result['_created'] else 200
        return result

    @app.delete('/v1/conversations/{conversation_id}', status_code=204, responses=errors)
    def delete_conversation(conversation_id: UUID, request: Request):
        store.delete_conversation(require_owner(request), str(conversation_id))
        return Response(status_code=204)

    @app.get('/v1/conversations/{conversation_id}/messages', response_model=MessagesPage, responses=errors,
             response_model_exclude_unset=True)
    def messages(conversation_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100),
                 before_seq: int | None = Query(None, ge=1)):
        owner = require_owner(request)
        page = store.list_turns(owner, str(conversation_id), limit=limit, before_seq=before_seq)
        return {'messages': [message for turn in page['turns'] for message in turn_messages(turn)],
                'next_before_seq': page['next_before_seq'], 'has_pending': page['has_pending'],
                'survey_context': store.get_conversation(owner, str(conversation_id))['survey_context']}

    @app.post('/v1/conversations/{conversation_id}/messages', response_model=TurnResponse, responses=errors,
              response_model_exclude_unset=True)
    def send_message(conversation_id: UUID, payload: ChatRequest, request: Request):
        owner = require_owner(request)
        cid = str(conversation_id)
        query = QueryRequest(query=payload.query, profile=payload.profile, top_k=payload.top_k)
        original = TurnInputSnapshot(query=payload.query, profile=payload.profile, top_k=payload.top_k, wallet_context=payload.wallet_context)
        reservation = store.reserve_turn(owner, cid, str(payload.client_request_id), original.model_dump(mode='json'), retry_failed=payload.retry_failed)
        turn, attempt = reservation['turn'], reservation['attempt_id']
        if not reservation['should_run']:
            return {'turn_id': turn['id'], 'messages': turn_messages(turn)}
        usage = {'provider_called': False, 'model': None, 'latency_ms': None, 'usage': None}
        try:
            history = store.completed_turns(owner, cid, before_seq=turn['seq'], include_insufficient=True)
            context = build_chat_context(history, payload.query, include_insufficient=True)
            references = card_references(history)
            if not references and history:
                # Older saved conversations do not have scope snapshots. Recover
                # their last successful recommendation lists without more LLM calls.
                anchors = store.completed_turns(owner, cid, before_seq=turn['seq'])
                references = card_references(sorted([*anchors, *history], key=lambda item: item['seq']))
            execution = ExecutionContext.model_validate(turn['execution_context']) if turn['execution_context'] else None
            survey = store.get_conversation(owner, cid)['survey_context']
            wallet = payload.wallet_context
            availability = {'survey_available': survey is not None, 'wallet_status': wallet.status if wallet else None,
                            'wallet_card_count': len(wallet.card_keys) if wallet else 0}
            first_gate = availability['survey_available'] or availability['wallet_status'] in ('ready', 'needs_review')
            need_rewrite = len(context) > 1 or first_gate
            if execution is None and not need_rewrite:
                question = missing_personalization_clarification(payload.query, availability['survey_available'], availability['wallet_status'])
                if question:
                    execution = clarification_context(payload.query, question)
            rewritten = RewriteOutput(standalone_query=payload.query)
            if execution is None and need_rewrite:
                started = time.perf_counter()
                usage.update(provider_called=True, model=getattr(provider, 'llm_model', None))
                try:
                    rewritten, rewrite_usage = provider.rewrite(context, references=[
                        {name: ref[name] for name in ('ref', 'card_name', 'issuer')} for ref in references],
                        personalization_availability=availability)
                    rewritten = RewriteOutput.model_validate(rewritten)
                    usage.update(model=rewrite_usage.get('model', usage['model']), usage=rewrite_usage.get('usage'))
                except Exception as error:
                    raise LlmUnavailable('대화 맥락을 반영한 질문을 만들지 못했습니다.') from error
                finally:
                    usage['latency_ms'] = round((time.perf_counter()-started)*1000, 3)
            if execution is None:
                active_keys = index_loader.load().manifest['document_ids'] if rewritten.use_wallet else ()
                execution = resolve_personalization(rewritten, survey, wallet, active_keys,
                    original_query=payload.query, references=references)
            store.save_execution_context(turn['id'], attempt, execution.model_dump(mode='json'))
            query = QueryRequest(query=execution.standalone_query, profile=payload.profile, top_k=payload.top_k)
            keys = tuple(execution.target_card_keys) if execution.target_card_keys is not None else None
            if execution.operation == 'recall':
                saved_refs = [{**item.model_dump(mode='json'), 'ref': item.source_ref} for item in execution.recall_items]
                answer = recall_answer([item.source_ref for item in execution.recall_items], saved_refs).model_dump(mode='json')
            else:
                personal = execution.personalization_context
                wallet_keys = tuple(personal.wallet_context.card_keys) if personal and personal.wallet_context else None
                search_operation = execution.wallet_operation
                if execution.wallet_compare_scope == 'all_owned' and keys is not None:
                    search_operation, wallet_keys = 'none', None
                answer = generate_answer(query, target_card_keys=keys, clarification=execution.clarification,
                    retrieval_query=execution.retrieval_query, wallet_card_keys=wallet_keys,
                    wallet_operation=search_operation,
                    personalization_context=personal.model_dump(mode='json') if personal else None).model_dump(mode='json')
            answer['usage']['answer']['conversation_scope'] = {
                'scope': execution.scope, 'target_card_keys': list(keys) if keys is not None else None,
                'operation': execution.operation, 'use_survey': execution.use_survey, 'use_wallet': execution.use_wallet,
                'wallet_operation': execution.wallet_operation,
                'reference_groups': reference_groups(references) if execution.scope != 'global' else [],
            }
        except Exception as error:
            if isinstance(error, ChatStoreError):
                failure = error
            elif isinstance(error, RagError):
                failure = ChatStoreError(503, error.code, '검색 또는 답변 생성 서비스를 사용할 수 없습니다.', error.retryable)
            elif isinstance(error, ValueError):
                failure = ChatStoreError(409, 'CONTRACT_MISMATCH', '요청과 활성 검색 설정이 일치하지 않습니다.')
            else:
                failure = ChatStoreError(503, 'INDEX_UNAVAILABLE', '검색 또는 답변 처리를 완료하지 못했습니다.', True)
            store.fail_turn(turn['id'], attempt, {'code': failure.code, 'message': failure.message,
                            'retryable': failure.retryable, 'request_id': request.state.request_id}, rewrite_usage=usage)
            raise failure from error
        # Deliberately outside the provider catch: failure here leaves a pending turn,
        # and never automatically repeats a paid provider call.
        completed = store.complete_turn(turn['id'], attempt, answer, standalone_query=query.query, rewrite_usage=usage)
        return {'turn_id': completed['id'], 'messages': turn_messages(completed)}
