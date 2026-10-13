"""The reference import has its own ledger and cannot mutate operational records."""
import csv
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest
from sqlalchemy import create_engine,text,event

from scripts.load_capacity_candidates import HEADERS,load_references,prepared_references
from tools.reference_intensity import ReferenceIntensityRequest,calculate_reference
from test_maritime_migrations import postgres_dsn,apply_initial,apply_maritime,execute

ROOT=Path(__file__).resolve().parents[2]
MIGRATION=ROOT/'migrations/20261013020000_capacity_candidates.sql'
VESSEL='REAL:IMO:9074729'


def candidate(**changes):
    row={key:'' for key in HEADERS}
    row.update(vessel_id=VESSEL,imo_number='9074729',wikidata_dwt_t='50000',
               candidate_dwt_t='50000',capacity_status='WIKIDATA_ONLY',
               use_restriction='REFERENCE_ONLY_NOT_STATUTORY_VERIFIED',
               wikidata_source_url='https://www.wikidata.org/',
               marine_vessels_source_url='https://github.com/rich-iannone/MarineVessels')
    return {**row,**changes}


def csv_file(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=HEADERS);writer.writeheader();writer.writerows(rows)
    return path


def apply_reference(dsn,direction='up'):
    sql=MIGRATION.read_text(encoding='utf-8')
    sql=sql.split('-- migrate:down')[0].split('-- migrate:up')[1] if direction=='up' else sql.split('-- migrate:down')[1]
    execute(dsn,sql)


def migrated(dsn):
    apply_initial(dsn);apply_maritime(dsn);apply_reference(dsn)
    with psycopg.connect(dsn) as db:
        db.execute('CREATE TABLE schema_migrations(version text PRIMARY KEY)')
        db.execute("INSERT INTO schema_migrations VALUES('20261013020000')")
        db.execute("INSERT INTO maritime_data.vessels(vessel_id,data_origin,imo_number,source_vessel_id) VALUES(%s,'REAL','9074729','9074729')",(VESSEL,))


def test_preview_never_connects_and_hashes_exact_file(tmp_path):
    path=csv_file(tmp_path/'capacity.csv',[candidate()])
    result=load_references('must not connect',path)
    assert result['rows']==1 and result['with_dwt']==1 and result['mode']=='preview'
    rows,digest=prepared_references(path)
    assert rows[0]['source_row']==2 and rows[0]['source_file_sha256']==digest
    assert rows[0]['source_json']['wikidata_dwt_t']=='50000'


@pytest.mark.parametrize('changes',[
    {'candidate_dwt_t':'40000'}, {'wikidata_dwt_t':'NaN'},
    {'candidate_dwt_t':'-5'}, {'vessel_id':'SYN:9074729'},
    {'capacity_status':'AGREE_WITHIN_5_PERCENT'},
    {'use_restriction':'VERIFIED'},
    {'marine_dwt_t':'90000','capacity_status':'AGREE_WITHIN_5_PERCENT'}])
def test_invalid_candidates_are_rejected_before_connection(tmp_path,changes):
    path=csv_file(tmp_path/'capacity.csv',[candidate(**changes)])
    with pytest.raises(ValueError):load_references('must not connect',path,apply=True)


def test_duplicate_identity_is_not_arbitrarily_selected(tmp_path):
    path=csv_file(tmp_path/'capacity.csv',[candidate(),candidate()])
    with pytest.raises(ValueError,match='duplicate'):prepared_references(path)


def test_empty_migration_rollback_and_reapply(postgres_dsn):
    apply_initial(postgres_dsn);apply_maritime(postgres_dsn)
    apply_reference(postgres_dsn);apply_reference(postgres_dsn,'down');apply_reference(postgres_dsn)


def test_apply_requires_ledger_and_preserves_existing_sources(postgres_dsn,tmp_path):
    path=csv_file(tmp_path/'capacity.csv',[candidate()])
    with pytest.raises(ValueError,match='migration'):load_references(postgres_dsn,path,apply=True)
    migrated(postgres_dsn)
    assert load_references(postgres_dsn,path,apply=True)['mode']=='loaded'
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute('SELECT count(*) FROM maritime_data.vessels').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM ships').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM maritime_data.annual_reports').fetchone()[0]==0
    with pytest.raises(ValueError,match='overwrite'):load_references(postgres_dsn,path,apply=True)
    with pytest.raises(psycopg.Error,match='not empty'):apply_reference(postgres_dsn,'down')


def test_unknown_real_identity_rolls_back_import(postgres_dsn,tmp_path):
    migrated(postgres_dsn)
    path=csv_file(tmp_path/'capacity.csv',[candidate(),candidate(vessel_id='REAL:IMO:1234567',imo_number='1234567')])
    with pytest.raises(ValueError,match='existing REAL'):load_references(postgres_dsn,path,apply=True)
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute('SELECT count(*) FROM maritime_data.capacity_candidates').fetchone()[0]==0


def test_mid_copy_failure_rolls_back_all_reference_rows(postgres_dsn,tmp_path):
    migrated(postgres_dsn)
    with psycopg.connect(postgres_dsn) as db:
        db.execute("INSERT INTO maritime_data.vessels(vessel_id,data_origin,imo_number,source_vessel_id) VALUES('REAL:IMO:1234567','REAL','1234567','1234567')")
        db.execute('''CREATE FUNCTION public.reject_reference() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.imo_number='1234567' THEN RAISE EXCEPTION 'fixture failure'; END IF; RETURN NEW; END $$''')
        db.execute('''CREATE TRIGGER reject_reference BEFORE INSERT ON maritime_data.capacity_candidates
            FOR EACH ROW EXECUTE FUNCTION public.reject_reference()''')
    path=csv_file(tmp_path/'capacity.csv',[candidate(),candidate(vessel_id='REAL:IMO:1234567',imo_number='1234567')])
    with pytest.raises(psycopg.Error,match='fixture failure'):load_references(postgres_dsn,path,apply=True)
    with psycopg.connect(postgres_dsn) as db:
        assert db.execute('SELECT count(*) FROM maritime_data.capacity_candidates').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM maritime_data.vessels').fetchone()[0]==2


def test_real_postgres_reference_join_and_read_only_snapshot(postgres_dsn,tmp_path):
    migrated(postgres_dsn)
    load_references(postgres_dsn,csv_file(tmp_path/'capacity.csv',[candidate()]),apply=True)
    with psycopg.connect(postgres_dsn) as db:
        db.execute("INSERT INTO maritime_data.source_files VALUES ('source','DS-005','fixture.csv',%s,NULL,'REAL','report','REAL_ANNUAL_QUERY',1,'[]')",('b'*64,))
        db.execute('''INSERT INTO maritime_data.annual_reports
            (record_id,source_id,source_sheet,source_row,vessel_id,ship_type,reporting_year,report_type,period_label,
             co2_t,distance_nm_estimate,quality_status,aggregate_eligible,provenance_json)
            VALUES ('r','source','CSV',2,%s,'Bulk carrier',2023,'FULL','2023',1000,100,'VALID',true,'{}')''',(VESSEL,))
    engine=create_engine('postgresql+psycopg://',creator=lambda:psycopg.connect(postgres_dsn))
    inspected=[]
    def inspect(conn,cursor,statement,params,context,many):
        if statement.lstrip().startswith('SELECT'):
            cursor.execute("SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation'),current_setting('statement_timeout')")
            inspected.append(cursor.fetchone())
    event.listen(engine,'before_cursor_execute',inspect)
    request=ReferenceIntensityRequest(scope={'dataset':'real_annual','vessel_id':VESSEL,'year':2023},accept_reference_limitations=True)
    try:
        result=calculate_reference(engine,request)
        assert result['reference_dwt_intensity']=='200.000000'
        assert len(inspected)==2 and all(tuple(x)==('on','repeatable read','5s') for x in inspected)
        assert result['capacity_reference']['source_json']['wikidata_dwt_t']=='50000'
        assert result['official_cii_rating'] is None
    finally:engine.dispose()
