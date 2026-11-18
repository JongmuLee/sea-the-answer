"""Select recorded positions without replacing a requested date with another day."""
from datetime import date, timedelta


def end_of_day(record):
    # Backwards-compatible marker emitted by the existing voyage simulator.
    return 'UTC 일자' in str(record.get('note') or '')


def select_record(records, *, today, requested=None):
    """A dated request is exact; a latest request excludes unfinished UTC snapshots."""
    for row in records:
        day = str(row['date'])
        if day > today or not str(row.get('position') or '').strip():
            continue
        if requested is not None and day != requested:
            continue
        if end_of_day(row) and day >= today:
            continue
        return row
    return None


def as_of(record):
    if not end_of_day(record):
        return None
    return (date.fromisoformat(str(record['date'])) + timedelta(days=1)).isoformat() + 'T00:00:00Z'
