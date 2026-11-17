"""Small, owner-checked continuation context; current data is always queried again."""
from uuid import UUID
import re


def starts_new_scope(question):
    return bool(re.search(r'새\s*질문|별개(?:의)?\s*질문|이전.{0,20}(?:무시|잊어|사용하지)|new question|ignore previous',
                          str(question or ''), re.I))


def previous_answer(ctx):
    if starts_new_scope(ctx.body.get('question')):
        return None
    parent = (ctx.body.get('context') or {}).get('previousQueryId')
    try:
        if not isinstance(parent, str) or str(UUID(parent)) != parent.lower():
            return None
    except ValueError:
        return None
    row = ctx.db.one('''SELECT q.response FROM queries q JOIN query_owner o ON o.id=q.id
        WHERE q.id=%s AND o.owner=%s''', (parent, ctx.user['id']))
    answer = row['response'] if row else None
    if not isinstance(answer, dict):
        return None
    conversation = ctx.body.get('conversationId')
    if conversation and answer.get('conversationId', answer.get('id')) != conversation:
        return None
    sources = [*answer.get('evidence', []), *answer.get('relatedEvidence', [])]
    if any(not ctx.db.can_read_document(ctx.user, item['document_id'])
           for item in sources if isinstance(item, dict) and item.get('document_id')):
        return None
    return answer


def previous_position(ctx, ships):
    answer = previous_answer(ctx)
    position = answer.get('position') if answer else None
    if not isinstance(position, dict) or not isinstance(position.get('ship'), dict):
        return None
    stored = position['ship']
    current = next((s for s in ships if s['id'] == stored.get('id')), None)
    if current is None or any(str(current.get(k, '')) != str(stored.get(k, ''))
                              for k in ('name', 'imo')):
        return None
    if stored.get('version') is not None and current.get('version') != stored['version']:
        return None
    selected = (ctx.body.get('context') or {}).get('ship')
    if selected and selected != current['id']:
        return None
    return position


def calendar_defaults(periods=(), years=()):
    """Fill omitted calendar parts only when the previous scope has a unique value."""
    dates = [p.start for p in periods] + [p.end for p in periods]
    year_values = {int(d[:4]) for d in dates} | set(years)
    month_values = {int(d[5:7]) for d in dates}
    return (next(iter(year_values)) if len(year_values) == 1 else None,
            next(iter(month_values)) if len(month_values) == 1 else None)
