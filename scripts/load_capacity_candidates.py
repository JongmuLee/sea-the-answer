"""Preview or atomically load the separate, non-statutory capacity reference CSV."""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re

from dotenv import load_dotenv
import psycopg
from psycopg.types.json import Jsonb

MIGRATION = '20261013020000'
HEADERS = ['vessel_id','imo_number','ship','wikidata_dwt_t','wikidata_gt','name','type',
           'built','marine_dwt_t','marine_gt','ship_draught','length','beam',
           'dwt_relative_difference','capacity_status','candidate_dwt_t','candidate_gt',
           'use_restriction','wikidata_source_url','marine_vessels_source_url']


def dwt(value: str) -> Decimal | None:
    if value == '':
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid reference DWT') from None
    if not number.is_finite() or not 0 < number < 10000000:
        raise ValueError('Reference DWT must be positive and finite')
    return number


def prepared_references(path: Path) -> tuple[list[dict], str]:
    """Read once: the exact hashed bytes are the only data subsequently inserted."""
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Reference CSV exceeds 64 MiB')
    with path.open('rb') as stream:
        raw = stream.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024:
        raise ValueError('Reference CSV exceeds 64 MiB')
    reader = csv.DictReader(StringIO(raw.decode('utf-8-sig'), newline=''))
    if reader.fieldnames != HEADERS:
        raise ValueError('Unexpected capacity reference CSV header')
    digest = hashlib.sha256(raw).hexdigest()
    rows, seen = [], set()
    with localcontext() as ctx:
        ctx.prec = 50
        for source_row, record in enumerate(reader, 2):
            if len(rows) >= 250000 or None in record or any(v is None for v in record.values()):
                raise ValueError('Reference CSV row width/count mismatch')
            imo = record['imo_number']
            vessel = record['vessel_id']
            if not re.fullmatch(r'[0-9]{7}', imo) or vessel != 'REAL:IMO:' + imo or vessel in seen:
                raise ValueError('Invalid or duplicate REAL reference identity')
            seen.add(vessel)
            wiki, marine, candidate = (dwt(record[k]) for k in
                                      ('wikidata_dwt_t','marine_dwt_t','candidate_dwt_t'))
            expected = wiki if wiki is not None else marine
            if candidate != expected:
                raise ValueError('Candidate DWT does not match declared source')
            status = 'UNAVAILABLE'
            if wiki is not None and marine is not None:
                difference = abs(wiki - marine) / max(wiki, marine)
                status = 'CONFLICT_OVER_5_PERCENT' if difference > Decimal('.05') else 'AGREE_WITHIN_5_PERCENT'
            elif wiki is not None:
                status = 'WIKIDATA_ONLY'
            elif marine is not None:
                status = 'MARINE_VESSELS_ONLY'
            if record['capacity_status'] != status:
                raise ValueError('Reference source agreement status is inconsistent')
            if record['use_restriction'] != 'REFERENCE_ONLY_NOT_STATUTORY_VERIFIED':
                raise ValueError('Statutory verification must not be inferred')
            if (record['wikidata_source_url'] != 'https://www.wikidata.org/'
                    or record['marine_vessels_source_url'] != 'https://github.com/rich-iannone/MarineVessels'):
                raise ValueError('Unknown capacity reference source')
            rows.append({'vessel_id':vessel, 'imo_number':imo, 'candidate_dwt_t':candidate,
                         'capacity_status':status, 'source_file_sha256':digest,
                         'source_filename':path.name, 'source_row':source_row, 'source_json':record})
    if not rows:
        raise ValueError('Reference CSV is empty')
    return rows, digest


def load_references(dsn: str, path: Path, *, apply: bool = False) -> dict:
    rows, digest = prepared_references(path)
    result = {'mode':'preview', 'rows':len(rows), 'with_dwt':sum(r['candidate_dwt_t'] is not None for r in rows),
              'conflicts':sum(r['capacity_status']=='CONFLICT_OVER_5_PERCENT' for r in rows),
              'source_file_sha256':digest, 'reference_only':True}
    if not apply:
        return result
    if not dsn:
        raise ValueError('DATABASE_URL is required')
    with psycopg.connect(dsn.replace('postgresql+psycopg://','postgresql://',1), connect_timeout=5) as db:
        db.execute("SET LOCAL lock_timeout='5s'")
        db.execute("SET LOCAL statement_timeout='120s'")
        if db.execute("SELECT to_regclass('public.schema_migrations')").fetchone()[0] is None:
            raise ValueError('Apply capacity dbmate migration first')
        if not db.execute('SELECT 1 FROM public.schema_migrations WHERE version=%s',(MIGRATION,)).fetchone():
            raise ValueError('Apply capacity dbmate migration first')
        db.execute('LOCK TABLE maritime_data.vessels IN SHARE MODE')
        db.execute('LOCK TABLE maritime_data.capacity_candidates IN ACCESS EXCLUSIVE MODE')
        if db.execute('SELECT EXISTS (SELECT FROM maritime_data.capacity_candidates)').fetchone()[0]:
            raise ValueError('Capacity references are not empty; refusing overwrite')
        real = dict(db.execute("SELECT vessel_id,imo_number FROM maritime_data.vessels WHERE data_origin='REAL'").fetchall())
        if any(real.get(r['vessel_id']) != r['imo_number'] for r in rows):
            raise ValueError('Reference identity is not an existing REAL vessel')
        with db.cursor().copy('''COPY maritime_data.capacity_candidates
             (vessel_id,imo_number,candidate_dwt_t,capacity_status,source_file_sha256,
              source_filename,source_row,source_json) FROM STDIN''') as copy:
            for r in rows:
                copy.write_row(tuple(Jsonb(r[k]) if k=='source_json' else r[k] for k in
                               ('vessel_id','imo_number','candidate_dwt_t','capacity_status',
                                'source_file_sha256','source_filename','source_row','source_json')))
        if db.execute('SELECT count(*) FROM maritime_data.capacity_candidates').fetchone()[0] != len(rows):
            raise ValueError('Loaded reference row count mismatch')
    return {**result, 'mode':'loaded'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,default=Path(__file__).resolve().parents[1]/'.env')
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    load_dotenv(args.env_file,override=False)
    try:
        print(json.dumps(load_references(os.getenv('DATABASE_URL',''),args.csv,apply=args.apply)))
    except (ValueError, OSError, UnicodeError, csv.Error, psycopg.Error):
        raise SystemExit('Capacity load failed. Check CSV, migration, REAL identities and empty reference table; no source data was overwritten.') from None


if __name__ == '__main__':
    main()
