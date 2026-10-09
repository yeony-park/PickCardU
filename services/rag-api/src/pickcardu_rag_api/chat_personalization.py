"""Resolve model intent against trusted conversation and per-turn settings."""
import unicodedata
import re

from pickcardu_rag.answering import RewriteOutput

from .chat_models import ExecutionContext, PersonalizationContext, SurveyContext, WalletContext, normalize_survey_context
from .chat_scope import CLARIFY_CARDS, resolve_scope


WALLET_REQUIRED = 'My Page에 카드를 먼저 등록하거나 등록 상태를 확인해 주세요.'
SURVEY_REQUIRED = '새 채팅에서 소비 패턴을 입력한 뒤 다시 질문해 주세요.'


def missing_personalization_clarification(query: str, survey_available: bool, wallet_status: str | None) -> str | None:
    text = ' '.join(unicodedata.normalize('NFKC', query).split())
    missing = []
    if wallet_status != 'ready' and re.search(r'(?<![가-힣A-Za-z0-9])(?:내 카드|보유 카드|등록 카드)', text):
        missing.append(WALLET_REQUIRED)
    if not survey_available and re.search(r'(?<![가-힣A-Za-z0-9])내 소비 패턴', text):
        missing.append(SURVEY_REQUIRED)
    return ' '.join(missing) or None


def effective_survey_context(survey: SurveyContext | None, overridden_fields: list[str]) -> SurveyContext | None:
    if survey is None:
        return None
    resets = {'monthly_spending': None, 'spending_categories': [], 'preferred_benefits': []}
    result = survey.model_copy(update={key: resets[key] for key in overridden_fields})
    normalized = normalize_survey_context(result)
    return SurveyContext.model_validate(normalized) if normalized is not None else None


def build_retrieval_query(standalone_query: str, survey: SurveyContext | None) -> str:
    hints = []
    if survey is not None:
        if survey.spending_categories:
            hints.append('소비 영역=' + ','.join(survey.spending_categories))
        if survey.preferred_benefits:
            hints.append('선호 혜택=' + ','.join(survey.preferred_benefits))
    query = standalone_query if not hints else standalone_query + '\n검색 힌트: ' + '; '.join(hints)
    if len(query) > 1024:
        raise ValueError('retrieval query exceeds the internal character limit')
    return query


def clarification_context(query: str, question: str) -> ExecutionContext:
    return ExecutionContext(standalone_query=query, scope='clarification', clarification=question)


def resolve_personalization(output: RewriteOutput, survey_context, wallet_context, active_card_keys,
                            *, original_query: str, references=None) -> ExecutionContext:
    output = RewriteOutput.model_validate(output)
    references = references or []
    keys, clarification = resolve_scope(output, references)
    if clarification:
        return clarification_context(original_query, clarification)
    if (len(set(output.overridden_survey_fields)) != len(output.overridden_survey_fields)
            or (not output.use_survey and output.overridden_survey_fields)
            or (not output.use_wallet and output.wallet_operation != 'none')
            or (output.use_wallet and output.wallet_operation == 'none')):
        return clarification_context(original_query, '어떤 소비 조건이나 보유 카드를 참고할까요?')
    if output.operation == 'recall':
        if output.use_survey or output.use_wallet:
            return clarification_context(original_query, CLARIFY_CARDS)
        items = [{'source_ref': ref['ref'], **{name: ref[name] for name in (
            'card_key', 'card_name', 'issuer', 'release_id', 'profile') if name in ref}}
            for ref in references if ref['ref'] in output.selected_refs]
        return ExecutionContext(standalone_query=output.standalone_query, scope='previous', operation='recall', recall_items=items)

    normalized = normalize_survey_context(survey_context) if output.use_survey else None
    if output.use_survey and normalized is None:
        return clarification_context(original_query, SURVEY_REQUIRED)
    survey = effective_survey_context(SurveyContext.model_validate(normalized), output.overridden_survey_fields) if normalized else None
    wallet = WalletContext.model_validate(wallet_context) if output.use_wallet and wallet_context is not None else None
    if output.use_wallet:
        if wallet is None or wallet.status != 'ready':
            return clarification_context(original_query, WALLET_REQUIRED)
        if any(key not in active_card_keys for key in wallet.card_keys):
            return clarification_context(original_query, '등록한 카드 중 현재 검색 자료와 일치하지 않는 카드가 있습니다. My Page 등록 상태를 확인해 주세요.')
        if output.wallet_compare_scope == 'all_owned':
            if len(wallet.card_keys) > 5:
                return clarification_context(original_query, '한 번에 정밀 비교할 카드를 최대 5개로 좁혀 주세요.')
            if keys is not None and set(keys) != set(wallet.card_keys):
                return clarification_context(original_query, '이전 추천 카드와 보유 카드 전체 중 어느 목록을 비교할까요?')
            keys = tuple(wallet.card_keys)
        elif output.wallet_operation == 'compare':
            new_keys = set(active_card_keys) - set(wallet.card_keys)
            if keys is not None:
                new_keys &= set(keys)
            if not new_keys:
                return clarification_context(original_query, '현재 비교 범위에 보유하지 않은 새 카드가 없어 보유/신규 비교를 할 수 없습니다.')
        elif keys is not None and not set(keys) <= set(wallet.card_keys):
            return clarification_context(original_query, '이전 추천 목록과 등록한 카드 목록이 다릅니다. 어떤 카드를 확인할까요?')

    generic = output.scope == 'global' and not output.use_survey and not output.use_wallet
    query = original_query if generic else output.standalone_query
    context = PersonalizationContext(survey_context=survey, wallet_context=wallet) if (
        output.use_survey or output.use_wallet) else None
    return ExecutionContext(standalone_query=query, retrieval_query=build_retrieval_query(query, survey),
        scope=output.scope, target_card_keys=list(keys) if keys is not None else None,
        use_survey=output.use_survey, use_wallet=output.use_wallet, wallet_operation=output.wallet_operation,
        wallet_compare_scope=output.wallet_compare_scope, overridden_survey_fields=output.overridden_survey_fields,
        personalization_context=context)
