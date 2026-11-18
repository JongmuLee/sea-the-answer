"""Match registered ship names and the project's explicit simulation labels.

These helpers do not resolve IDs or IMO numbers. Callers keep their existing
identifier validation, ownership checks and current ship snapshots.
"""
import re


_SIMULATION_PREFIX = re.compile(r'^가상\s*선박\s+')
_ROUND_TRIP_SUFFIX = re.compile(r'\s*·\s*왕복\s*시나리오\s*$')
_NAME_END = r'(?=$|[\s,/.·?!]|(?:으로|의|와|과|를|을|는|은|도|에|로|이|가)(?:\s|[?.!]|$))'


def _compact(value):
    return re.sub(r'\s+', '', str(value or '').casefold())


def name_aliases(name):
    """Return whitespace-insensitive full names and explicitly labelled aliases.

    Only the leading ``가상 선박`` and trailing ``· 왕복 시나리오``
    decorations are removable. Every other word, number and symbol is retained.
    """
    full = str(name or '').strip().casefold()
    without_prefix = _SIMULATION_PREFIX.sub('', full)
    core = _ROUND_TRIP_SUFFIX.sub('', without_prefix).strip()
    variants = [full]
    if len(_compact(core)) >= 2:
        variants.extend((without_prefix, _ROUND_TRIP_SUFFIX.sub('', full), core))
    return tuple(dict.fromkeys(_compact(value) for value in variants if _compact(value)))


def _unique_name(candidates):
    if len({ship['id'] for ship in candidates}) > 1:
        raise ValueError('같은 이름이나 약칭의 선박이 여러 척입니다. IMO 번호 또는 선박 선택으로 구분해 주세요.')
    return candidates[0]['name'] if candidates else None


def resolve_ship_name(mention, ships):
    """Return the registered full name for one exact name or alias, else None.

    Shared names or aliases raise ValueError. An IMO or internal ID needs the
    caller's original identifier path and is never matched against those fields.
    """
    value = _compact(mention)
    if not value:
        return None
    return _unique_name([ship for ship in ships if value in name_aliases(ship['name'])])


def named_ships(question, ships, *, allow_ambiguous=False):
    """Find explicitly named ships, respecting word boundaries and particles.

    A longer qualified name takes precedence over names contained inside it.
    Shared aliases raise ValueError unless the caller will disambiguate using
    already validated IMO numbers and explicitly enables allow_ambiguous.
    """
    text = str(question or '').casefold()
    occurrences = []
    for ship in ships:
        for alias in name_aliases(ship['name']):
            if len(alias) < 2:
                continue
            pattern = r'(?<![\w-])' + r'\s*'.join(map(re.escape, alias)) + _NAME_END
            occurrences.extend((match.start(), match.end(), ship) for match in re.finditer(pattern, text))

    qualified = [(start, end, ship) for start, end, ship in occurrences
                 if not any(other_start <= start and end <= other_end
                            and (other_start, other_end) != (start, end)
                            for other_start, other_end, _ in occurrences)]
    if not allow_ambiguous:
        by_span = {}
        for start, end, ship in qualified:
            by_span.setdefault((start, end), []).append(ship)
        for candidates in by_span.values():
            _unique_name(candidates)
    identifiers = {ship['id'] for _, _, ship in qualified}
    return [ship for ship in ships if ship['id'] in identifiers]
