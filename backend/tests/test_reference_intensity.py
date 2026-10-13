"""Reference-only arithmetic preserves provenance and never yields official CII."""
from copy import deepcopy
from decimal import Decimal
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from fastapi import HTTPException

from tools.api import create_app
from tools import api
from tools.reference_intensity import ReferenceIntensityRequest, compute_reference, calculate_reference

VESSEL='REAL:IMO:9074729'
TOKEN='reference-test-only-token-123456789012345678901234'


def body(**extra):
    return {'scope':{'dataset':'real_annual','vessel_id':VESSEL,'year':2023},
            'accept_reference_limitations':True,**extra}


def record(**extra):
    return {'vessel_id':VESSEL,'reporting_year':2023,'ship_type':'Bulk carrier',
            'report_type':'FULL','quality_status':'VALID','aggregate_eligible':True,
            'data_origin':'REAL','co2_t':Decimal('1000'),'distance_nm_estimate':Decimal('100'),
            'dwt_t':None,'source_sha256':'b'*64,'source_row':8,**extra}


def reference(**extra):
    return {'vessel_id':VESSEL,'imo_number':'9074729','candidate_dwt_t':Decimal('50000'),
            'capacity_status':'WIKIDATA_ONLY','source_file_sha256':'a'*64,'source_row':2,
            'source_json':{'wikidata_dwt_t':'50000','marine_dwt_t':'',
                           'capacity_status':'WIKIDATA_ONLY',
                           'use_restriction':'REFERENCE_ONLY_NOT_STATUTORY_VERIFIED'},**extra}


def test_reference_arithmetic_keeps_originals_and_auditable_snapshots():
    rows,ref=[record()],reference()
    before=deepcopy((rows,ref))
    result=compute_reference(rows,ref,ReferenceIntensityRequest(**body()))
    assert result['reference_dwt_intensity']=='200.000000'
    assert result['status']=='estimated' and result['reference_only'] and result['requires_review']
    assert result['official_cii'] is result['official_cii_rating'] is None
    assert result['compliance']=='not_assessed'
    assert result['input_snapshot'][0]['source_sha256']=='b'*64
    assert result['capacity_reference']['source_file_sha256']=='a'*64
    assert 'CO2_DISTANCE_SCOPE_ALIGNMENT_UNVERIFIED' in result['warnings']
    assert 'CAPACITY_AT_REPORTING_YEAR_UNVERIFIED' in result['warnings']
    assert (rows,ref)==before


@pytest.mark.parametrize('change,reason',[
    ({'ship_type':'Ro-ro ship'},'SHIP_TYPE_CAPACITY_BASIS_UNSUPPORTED'),
    ({'co2_t':None},'MISSING_OR_NONPOSITIVE_REPORTED_CO2'),
    ({'co2_t':0},'MISSING_OR_NONPOSITIVE_REPORTED_CO2'),
    ({'distance_nm_estimate':0},'MISSING_OR_NONPOSITIVE_ESTIMATED_DISTANCE'),
    ({'distance_nm_estimate':Decimal('NaN')},'MISSING_OR_NONPOSITIVE_ESTIMATED_DISTANCE'),
    ({'distance_nm_estimate':Decimal('1e-1000')},'NUMERIC_RANGE_REQUIRES_REVIEW'),
    ({'dwt_t':Decimal('10000')},'SOURCE_AND_REFERENCE_DWT_CONFLICT'),
    ({'report_type':'PARTIAL'},'ANNUAL_SCOPE_OR_QUALITY_REQUIRES_REVIEW'),
    ({'quality_status':'REVIEW'},'ANNUAL_SCOPE_OR_QUALITY_REQUIRES_REVIEW'),
    ({'reporting_year':2022},'ANNUAL_SCOPE_OR_QUALITY_REQUIRES_REVIEW')])
def test_unsuitable_source_is_blocked(change,reason):
    result=compute_reference([record(**change)],reference(),ReferenceIntensityRequest(**body()))
    assert result['status']=='blocked' and result['reference_dwt_intensity'] is None
    assert reason in result['blockers']
    if change.get('co2_t') == 0:
        assert result['source_reported_co2_t']=='0'
    if change.get('distance_nm_estimate') == 0:
        assert result['distance_nm_estimate']=='0'


@pytest.mark.parametrize('ref,reason',[
    (None,'CAPACITY_REFERENCE_UNAVAILABLE'),
    (reference(capacity_status='CONFLICT_OVER_5_PERCENT'),'CAPACITY_SOURCE_CONFLICT'),
    (reference(candidate_dwt_t=0),'MISSING_OR_INVALID_REFERENCE_DWT'),
    (reference(candidate_dwt_t=Decimal('90000')),'INVALID_CAPACITY_PROVENANCE'),
    (reference(imo_number='1111111'),'CAPACITY_IDENTITY_MISMATCH')])
def test_unsuitable_reference_is_blocked(ref,reason):
    result=compute_reference([record()],ref,ReferenceIntensityRequest(**body()))
    assert reason in result['blockers'] and result['reference_dwt_intensity'] is None


def test_multiple_records_are_never_summed_or_arbitrarily_selected():
    result=compute_reference([record(),record()],reference(),ReferenceIntensityRequest(**body()))
    assert 'MULTIPLE_ANNUAL_RECORDS_REQUIRE_REVIEW' in result['blockers']
    assert result['source_reported_co2_t'] is None


def test_db_errors_do_not_expose_sql_or_credentials():
    engine=MagicMock()
    engine.dialect.name='postgresql'
    engine.connect.side_effect=OperationalError('PRIVATE SQL',{},Exception('PRIVATE PASSWORD'))
    with pytest.raises(HTTPException) as exc:
        calculate_reference(engine,ReferenceIntensityRequest(**body()))
    assert exc.value.status_code==503 and 'PRIVATE' not in exc.value.detail


@pytest.mark.parametrize('marine,blocked',[('47500',False),('47499',True)])
def test_source_conflict_boundary_uses_maximum_capacity_denominator(marine,blocked):
    ref=reference()
    ref['source_json']['marine_dwt_t']=marine
    ref['capacity_status']=ref['source_json']['capacity_status']='AGREE_WITHIN_5_PERCENT'
    result=compute_reference([record()],ref,ReferenceIntensityRequest(**body()))
    assert (result['reference_dwt_intensity'] is None)==blocked
    if blocked:assert 'CAPACITY_SOURCE_CONFLICT' in result['blockers']


@pytest.mark.parametrize('value',[False,1,'true',None])
def test_reference_acknowledgement_must_be_explicit_boolean(value):
    with pytest.raises(ValidationError):ReferenceIntensityRequest(**body(accept_reference_limitations=value))


def test_api_auth_scope_and_db_failure_are_explicit(monkeypatch):
    app=create_app('sqlite://',TOKEN)
    try:
        with TestClient(app) as client:
            route='/api/maritime-data/reference-intensity'
            assert client.post(route,json=body()).status_code==401
            assert client.post(route,json=body(),headers={'Authorization':'Bearer bad'}).status_code==403
            client.headers['Authorization']='Bearer '+TOKEN
            assert client.post(route,json=body(dwt=50000)).status_code==422
            assert client.post(route,json=body(scope={'dataset':'synthetic_noon'})).status_code==422
            assert client.post(route,json=body()).status_code==503
            monkeypatch.setattr(api,'calculate_reference',lambda engine,request:{'status':'estimated','reference_only':True})
            assert client.post(route,json=body()).json()=={'status':'estimated','reference_only':True}
    finally:app.state.engine.dispose()
