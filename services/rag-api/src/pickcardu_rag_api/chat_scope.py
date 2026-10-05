"""Resolve short model-selected references against owned, server-stored cards."""
from pickcardu_rag.answering import RewriteOutput


CLARIFY_CARDS = '어떤 카드들을 확인할까요? 카드 이름이나 이전 추천 목록의 번호를 알려 주세요.'


def card_references(turns: list[dict]) -> list[dict[str, str]]:
    groups = []
    for turn in turns:
        if turn.get('state') != 'completed':
            continue
        answer = turn.get('answer') or {}
        scope = answer.get('usage', {}).get('answer', {}).get('conversation_scope', {})
        if scope.get('scope') in ('previous', 'clarification'):
            candidates = scope.get('reference_groups', [])
            # The newer snapshot is authoritative; merging it into an older
            # partial group can reverse the order of recommendation lists.
            groups = []
        elif answer.get('answer_status') == 'answered':
            cards = {card['card_key']: card for card in answer.get('cards', [])}
            candidates = [[{name: cards[rec['card_key']][name] for name in ('card_key', 'card_name', 'issuer')}
                           for rec in answer.get('recommendations', []) if rec['card_key'] in cards]]
        else:
            candidates = []
        for group in candidates:
            if group:
                group = group[:5]
                if group in groups:
                    groups.remove(group)
                groups.append(group)
    return [{**card, 'ref': f't{group_index}r{card_index}'}
            for group_index, group in enumerate(groups[-2:], 1)
            for card_index, card in enumerate(group, 1)]


def reference_groups(references: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    groups = {}
    for ref in references:
        group = ref['ref'].split('r')[0]
        groups.setdefault(group, []).append({name: ref[name] for name in ('card_key', 'card_name', 'issuer')})
    return list(groups.values())


def resolve_scope(output: RewriteOutput, references: list[dict[str, str]]) -> tuple[tuple[str, ...] | None, str | None]:
    if output.scope == 'clarification':
        return None, output.clarification_question.strip() or CLARIFY_CARDS
    if output.scope == 'global':
        return (None, None) if not output.selected_refs and not output.clarification_question.strip() else (None, CLARIFY_CARDS)
    by_ref = {ref['ref']: ref['card_key'] for ref in references}
    selected = output.selected_refs
    if (not selected or len(set(selected)) != len(selected)
        or any(ref not in by_ref for ref in selected)
        or len({ref.split('r')[0] for ref in selected}) != 1
        or output.clarification_question.strip()):
        return None, CLARIFY_CARDS
    # Model selection order is not trusted as the recommendation order.
    keys = tuple(key for ref, key in by_ref.items() if ref in selected)
    return (keys, None) if len(set(keys)) == len(keys) else (None, CLARIFY_CARDS)
