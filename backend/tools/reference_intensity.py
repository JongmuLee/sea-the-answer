"""MRV-based reference arithmetic; never an attained annual IMO CII or rating."""
from __future__ import annotations

from decimal import Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Literal

from fastapi import HTTPException
from pydantic import field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .calculations import AnnualScope, Strict
from .query import json_value

VERSION = 'MRV-REFERENCE-DWT-INTENSITY-1.0'
SUPPORTED_TYPES = {'Bulk carrier','Container ship','Oil tanker'}


class ReferenceIntensityRequest(Strict):
    scope: AnnualScope
    accept_reference_limitations: Literal[True]

    @field_validator('accept_reference_limitations', mode='before')
    @classmethod
    def strict_acceptance(cls, value):
        if value is not True:
            raise ValueError('Explicit boolean true is required for reference-only arithmetic')
        return value


def positive(value):
    if value is None or isinstance(value,bool):
        return None
    try:
        number = Decimal(str(value))
    except (ValueError,InvalidOperation):
        return None
    return number if number.is_finite() and 0 < number < Decimal('1e18') else None


def capacity_problem(reference):
    if not reference:
        return 'CAPACITY_REFERENCE_UNAVAILABLE'
    if reference['capacity_status'] == 'CONFLICT_OVER_5_PERCENT':
        return 'CAPACITY_SOURCE_CONFLICT'
    source = reference.get('source_json') or {}
    wiki = positive(source.get('wikidata_dwt_t'))
    marine = positive(source.get('marine_dwt_t'))
    candidate = positive(reference.get('candidate_dwt_t'))
    if candidate is None:
        return 'MISSING_OR_INVALID_REFERENCE_DWT'
    if source.get('use_restriction') != 'REFERENCE_ONLY_NOT_STATUTORY_VERIFIED':
        return 'INVALID_CAPACITY_PROVENANCE'
    if candidate != (wiki if wiki is not None else marine):
        return 'INVALID_CAPACITY_PROVENANCE'
    if wiki and marine and abs(wiki-marine)/max(wiki,marine) > Decimal('.05'):
        return 'CAPACITY_SOURCE_CONFLICT'
    if source.get('capacity_status') != reference['capacity_status']:
        return 'INVALID_CAPACITY_PROVENANCE'
    return None


def compute_reference(records, reference, request):
    warnings = ['REFERENCE_ONLY_NOT_OFFICIAL_CII','MRV_SCOPE_NOT_VERIFIED_AS_GLOBAL_ANNUAL_DCS',
                'DISTANCE_DERIVED_FROM_FUEL_INTENSITY','CO2_DISTANCE_SCOPE_ALIGNMENT_UNVERIFIED',
                'CAPACITY_CANDIDATE_NOT_STATUTORY_VERIFIED','CAPACITY_AT_REPORTING_YEAR_UNVERIFIED',
                'NO_CII_CORRECTIONS_OR_RATING_ASSESSMENT']
    blockers = []
    reported = distance = dwt = intensity = None
    with localcontext() as ctx:
        ctx.prec = 50
        if not records:
            blockers.append('NO_ELIGIBLE_ANNUAL_RECORD')
        elif len(records) != 1:
            blockers.append('MULTIPLE_ANNUAL_RECORDS_REQUIRE_REVIEW')
        else:
            row = records[0]
            if (row.get('vessel_id') != request.scope.vessel_id or row.get('reporting_year') != request.scope.year
                    or row.get('report_type') not in {'FULL','ARCHIVE_ANNUAL'}
                    or row.get('quality_status') != 'VALID' or row.get('aggregate_eligible') is not True
                    or row.get('data_origin') != 'REAL'):
                blockers.append('ANNUAL_SCOPE_OR_QUALITY_REQUIRES_REVIEW')
            if row.get('ship_type') not in SUPPORTED_TYPES:
                blockers.append('SHIP_TYPE_CAPACITY_BASIS_UNSUPPORTED')
            reported = positive(row.get('co2_t'))
            distance = positive(row.get('distance_nm_estimate'))
            if reported is None:
                blockers.append('MISSING_OR_NONPOSITIVE_REPORTED_CO2')
            if distance is None:
                blockers.append('MISSING_OR_NONPOSITIVE_ESTIMATED_DISTANCE')
        problem = capacity_problem(reference)
        if problem:
            blockers.append(problem)
        elif reference['vessel_id'] != request.scope.vessel_id or reference['imo_number'] != request.scope.vessel_id.removeprefix('REAL:IMO:'):
            blockers.append('CAPACITY_IDENTITY_MISMATCH')
        else:
            dwt = positive(reference['candidate_dwt_t'])
            if len(records)==1 and records[0].get('dwt_t') is not None:
                source_dwt = positive(records[0]['dwt_t'])
                if source_dwt is None or abs(source_dwt-dwt)/max(source_dwt,dwt) > Decimal('.05'):
                    blockers.append('SOURCE_AND_REFERENCE_DWT_CONFLICT')
            if reference['capacity_status'] in {'WIKIDATA_ONLY','MARINE_VESSELS_ONLY'}:
                warnings.append('SINGLE_SOURCE_CAPACITY_CANDIDATE')
        if not blockers:
            try:
                intensity = (reported*Decimal(1000000)/(dwt*distance)).quantize(Decimal('.000001'),rounding=ROUND_HALF_UP)
            except DecimalException:
                blockers.append('NUMERIC_RANGE_REQUIRES_REVIEW')
    return {'tool':'mrv_reference_dwt_intensity','version':VERSION,
            'label':'MRV 기반 참고 탄소집약도 (공식 CII 아님)',
            'scope':request.scope.model_dump(), 'status':'estimated' if intensity is not None else 'blocked',
            'reference_only':True,'requires_review':True,'data_origin':'REAL',
            'record_count':len(records),'reference_dwt_intensity':str(intensity) if intensity is not None else None,
            'source_reported_co2_t':str(records[0]['co2_t']) if len(records)==1 and records[0].get('co2_t') is not None else None,
            'distance_nm_estimate':str(records[0]['distance_nm_estimate']) if len(records)==1 and records[0].get('distance_nm_estimate') is not None else None,
            'candidate_dwt_t':str(dwt) if dwt is not None else None,
            'formula':'source_reported_co2_t * 1000000 / (candidate_dwt_t * distance_nm_estimate)',
            'units':{'reference_dwt_intensity':'gCO2/(DWT t * nm)','source_reported_co2_t':'t CO2',
                     'distance_nm_estimate':'nm','candidate_dwt_t':'t'},
            'rounding':'Decimal precision=50; final 6 places ROUND_HALF_UP',
            'blockers':blockers,'warnings':warnings,
            'official_cii':None,'official_cii_rating':None,'compliance':'not_assessed',
            'request_snapshot':request.model_dump(), 'input_snapshot':json_value(records),
            'capacity_reference':json_value(reference)}


def calculate_reference(engine, request):
    if engine.dialect.name != 'postgresql':
        raise HTTPException(503,'PostgreSQL 해사 원본 및 제원 참조 적재가 필요합니다.')
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            rows = connection.execute(text('''SELECT r.*,s.sha256 AS source_sha256,
                s.filename AS source_filename,s.source_url,s.data_origin
                FROM maritime_data.real_annual_query r JOIN maritime_data.source_files s USING(source_id)
                WHERE r.vessel_id=:vessel_id AND r.reporting_year=:year ORDER BY r.record_id LIMIT 1001'''),
                request.scope.model_dump()).mappings().all()
            if len(rows)>1000:
                raise HTTPException(422,'1000행을 초과하는 입력은 계산하지 않습니다.')
            reference = connection.execute(text('''SELECT * FROM maritime_data.capacity_candidates
                WHERE vessel_id=:vessel_id'''),{'vessel_id':request.scope.vessel_id}).mappings().one_or_none()
    except SQLAlchemyError:
        raise HTTPException(503,'참고 지표의 DB·migration·원본 및 제원 적재 상태를 확인하세요.') from None
    return compute_reference([dict(r) for r in rows],dict(reference) if reference else None,request)
