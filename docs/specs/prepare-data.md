# 사전 데이터 수집·전처리·적재 계약 v1

- 상태: 구현됨
- 작성: 역할4 · 2026-10-08 (기존 2026-09-30 계약의 문서 위치 정리)
- 관련: [API 명세](tools.md), [데이터 모델](../data-model.md#9-postgresql-해사-데이터-tool-선택), [운영 안내](../user-guide.md#10-해사-데이터-tool-선택), [통합 계획](../plans/2026-10-13-tools-and-data-preparation-integration.md), [ADR 0011 제안](../adr/0011-tools-runtime-and-data-preparation.md)

## 목적과 범위

원본을 실제 MRV·개발용 합성 Noon·참조 자료로 구분하고, 전처리 결과의 식별자·출처·결측·품질 및 집계 가능 여부를 정의합니다. 구현 기준은 `scripts/prepare_maritime_data.py`입니다. PostgreSQL 테이블·컬럼·제약·ERD는 데이터 모델에 한 번만 정의합니다.

항구 자동 매핑, 공개 제원 후보의 법정 검증값 승격, 공식 CII 입력 확보·등급 산정, 팀 앱 테이블 교체는 포함하지 않습니다. CO₂ Tool이 사용하는 고정 계수 부분집합은 API 명세에 정의되어 있으며 최신 규정 전체의 구현을 뜻하지 않습니다.

## 사전 자료 확보와 처리 책임

- 원본 MRV·Synthetic Noon 파일과 공개 참조 자료는 이용조건·접근 권한을 확인한 뒤 별도로 확보합니다. 서버 시작이나 마이그레이션이 자동 다운로드하지 않습니다.
- `scripts/prepare_maritime_data.py`: 확보한 원본을 실제·합성으로 분리하여 CSV·출처·품질정보를 생성합니다.
- `scripts/prepare_gisis_eedi.py`, `scripts/prepare_open_cii_references.py`: 확보한 GISIS EEDI·공개 참조 자료를 별도 참조 데이터로 정규화합니다. 운영 데이터로 자동 합치지 않습니다.
- `scripts/load_data.py`: 마이그레이션이 적용된 빈 테이블에 전처리 결과를 적재합니다. `scripts/link_ship.py`는 검토한 앱 선박 연결만 적용합니다.
- 입력 파일의 위치·출처·이용조건과 실행 명령은 [운영 안내](../user-guide.md#10-해사-데이터-tool-선택)를 따릅니다. 실행 중 Tool 호출 계약은 [tools.md](tools.md)에서 관리합니다.

## 용도와 경계

- DS-001/005: 실제 보고기간 집계 조회. 연간 자료를 일별·항차별로 분해하지 않습니다.
- DS-006: 개발용으로 선정. 가상 표식을 유지한 채 일별 조회·항차 집계·Tool 테스트에 사용합니다.
- DS-003: 항구 기준정보로 보존. 항구명만 보고 자동 조인하지 않습니다.
- DS-004: 컬럼 설계 참고. 운항 관측 데이터에 포함하지 않습니다.
- DS-002: 마스킹 AIS 참고. 좌표 스케일을 추정 변환하거나 실제 선박과 연결하지 않습니다.

## 키·결측·중복 정책

- 실제 선박 키 `REAL:IMO:번호`, 가상 선박 키 `SYN:원본ID`로 충돌을 방지합니다. 실제 IMO 형식·체크섬을 검사하고 발급 여부는 검증하지 않습니다.
- 원본 파일 SHA256, 시트, 행으로 출처를 추적합니다. CSV 행 번호는 줄바꿈을 포함하는 물리 행이 아닌 논리 레코드 번호입니다.
- `raw_records.jsonl`은 모든 원본 관측 레코드와 안정적인 record_id를 보존합니다. XLSX 중복 헤더는 순서 배열로 보존합니다.
- 빈칸·N/A·원본 나눗셈 오류·음수·비정상 숫자를 구분합니다. 빈 CSV 셀은 DB NULL이며 0은 실제 원본 0입니다.
- 역사 MRV 운항시간은 명시적 원본 별칭을 확인합니다. 별칭 간 값이 다르면 선택하지 않고 결측·충돌로 남깁니다.
- 역산 거리는 추정치이며 원본 총량과 거리당 지표의 범위 일치가 검증되지 않았습니다. 공식 계산이나 독립 검증의 정답으로 사용하지 않습니다.
- Partial 보고서는 원본별 보존하고 Full과 합산하지 않습니다. 선박·연도·보고유형 중복의 모든 행을 REVIEW로 처리합니다.
- 가상 항차 첫/마지막 보고일을 실제 입출항 시각으로 바꾸지 않습니다. 2025 첫 Noon 구간은 2024-12-31에 시작할 수 있습니다.
- VALID는 지정 전처리 규칙 통과일 뿐 원본 사실성·규정 적합성의 인증이 아닙니다. REVIEW/REJECT는 기본 집계에서 제외합니다.

## 전처리 산출물과 저장 계약

출력 폴더에는 `source_files.csv`, `vessels.csv`, `annual_reports.csv`, `synthetic_noon.csv`, `synthetic_voyages.csv`, `quality_issues.csv` 및 `summary.json`, `raw_records.jsonl`을 보존합니다. CSV의 빈 셀은 적재 시 NULL로 해석하고 JSON 컬럼은 JSONB로 적재합니다. 원본 버전·출처와 요약은 데이터와 함께 전달하며 저장소에 원본을 재배포하지 않습니다.

DDL의 유일한 원본은 루트 `migrations/20261013010000_maritime_data.sql`입니다. `scripts/describe_maritime_data.py`는 전처리 `FIELDS`의 기계용 필드 사전만 생성하고 DDL·마이그레이션·수동 명세·ERD는 덮어쓰지 않습니다. 전처리 CSV의 `vessels`에는 원본 필드만 담고 관리용 nullable `ship_id`는 적재 후 별도 명시 매핑으로 연결합니다. 원본 파일명과 참고자료의 확보 범위는 운영 안내에서 관리합니다.

## 적재 전 확인

마이그레이션을 먼저 적용하여 앱과 같은 DB에 `maritime_data` 테이블을 준비합니다. 별도 DB를 선택한 경우에도 기본 init과 해사 마이그레이션을 모두 적용해야 합니다. 이전 단일 스키마는 구조 검증 후 채택하며 양쪽 스키마 충돌은 거절합니다. CSV loader는 기본 미리보기에서 DB에 연결하지 않고 `--apply`에서만 migration 이력과 빈 대상 테이블을 확인합니다. DDL은 실행하지 않으며 실패 시 전체 롤백합니다. 마이그레이션 자체는 원본을 확보하거나 CSV를 적재하지 않습니다. 기존 앱 화면·Agent는 이를 자동 조회하지 않습니다. 조회 계약은 [API 명세](tools.md), 실행·기존 적재 기록은 [운영 안내](../user-guide.md#10-해사-데이터-tool-선택)를 확인하세요.
정상·검토 레코드와 거절 레코드의 적재 경로를 나누고 필수값이 없는 REJECT는 원본/이슈 저장소에만 보관하세요. INSERT 시 숫자·날짜를 파싱하고 JSON 필드는 JSONB로 적재합니다. 원본 판정 충족률을 공식 CII 준비율로 사용하지 않습니다.

## 수용 기준

### 제원 후보 참조 적재

`scripts.load_capacity_candidates --csv <vessel_capacity_candidates.csv>`는 DB 연결 없는 미리보기다. `--apply`는 추가 migration `20261013020000`과 기존 REAL 선박을 확인하고 빈 capacity_candidates에만 전체를 원자적으로 적재한다. 준비 CSV는 한 번 읽은 바이트를 검증·해시·적재하므로 검증 이후 파일 변경이 결과에 섞이지 않는다. 최대 64MiB·250,000행이며 중복·형식·비유한/비양수 DWT·출처 불일치·후보 선택 불일치·검증값 승격을 거절한다.

후보가 없는 선박과 출처 충돌 선박도 상태와 출처를 보존하지만 참고 지표 계산 대상에서 제외한다. 후보에는 보고연도 기준 검증이 없고 MarineVessels 출처는 2015년 자료다. CSV·MRV·기존 제원은 덮어쓰지 않는다. 운영 데이터 적재기는 기존 6개 CSV 계약을 유지한다.

- 실제·합성 선박의 식별자와 데이터 출처를 혼합하지 않는다.
- NULL·원본 0·역산 추정값을 구분하고 각 원본 레코드의 출처를 추적할 수 있다.
- Partial 보고서와 중복·검토 레코드를 기본 집계에서 제외하고 원본·품질 이슈는 보존한다.
- 동일 입력의 전처리와 필드 사전 생성 결과를 재현할 수 있다. DDL은 버전별 마이그레이션으로만 변경한다.
- 앱 선박 연결은 검토한 명시적 매핑만 허용하며, 외부 선박 대량 등록이나 연간 MRV의 일별 분해를 하지 않는다.

## 열린 질문

- 항구·팀 선박 식별자 매핑과 검증된 실제 선박 제원·연간 CII 입력의 확보는 후속 데이터·팀 연결 단계에서 확정한다.

## 항구 참조 연결 시범 (2026-11-04)

상태: 로컬 프로토타입. [연결 검증 계획·결과](../plans/2026-11-04-port-reference-linkage.md).

`scripts.prepare_port_references`는 기존 실제 MRV 선박 목록·MarineVessels·WPI를 읽고 별도 연구 파일을 만든다. 기존 전처리 6개 CSV·운영 DB·API 계약을 바꾸지 않는다. 네트워크 수집은 명시적 `--fetch-wikidata`에서만 수행한다.

- 실제 선박은 `REAL:IMO:<7자리 번호>`와 체크섬이 맞는 IMO로만 연결한다. 합성 선박·이름 유사도 매핑은 제외한다. MarineVessels의 동일 IMO가 여러 행이면 원본 행을 보존하고 `ambiguous`로 둔다. 원본 문서에 단위·측정 기준이 충분히 명시되지 않은 길이·폭·흘수는 자동 계산에 사용하지 않는다.
- Wikidata 표본은 원문 명제 ID·수량·단위·rank·조회 시각·쿼리·SHA256을 보존한다. 단위는 m/cm/mm/ft만 명시 변환한다. 서로 다른 수량·미지원 단위·동일 IMO의 복수 entity는 자동 선택하지 않는다. 명제의 유효 시점·길이 기준(LOA 등)은 검증되지 않았다. deprecated 명제는 수집 쿼리에서 제외한다.
- WPI의 길이·폭·최대 흘수·수심은 서로 다른 필드다. 양수가 아닌 제한값은 미확인으로 두며 무제한으로 보지 않는다. 좌표는 도 단위 범위를 검사한다. 항구명으로 조인하지 않고 WPI 번호를 사용하되 중복 WPI는 비교를 거절한다. 중복 UN/LOCODE도 임의 선택하지 않는다.
- `ship-references.jsonl`에는 미연결 선박도 남겨 전체 분모를 보존한다. `port-references.json`, `linkage-summary.json`과 원본 Wikidata 응답·manifest·query를 함께 보관한다. 재실행은 snapshot SHA256과 쿼리 내용 해시(줄바꿈 LF 정규화)를 검사한다. 표본 선택은 비무작위이며 전체 제원 확보율로 환산하지 않는다.
- 순수 함수 `backend/tools/port_reference.py`는 단위가 확인된 Wikidata 길이·폭을 WPI의 명시적 제한값과 비교한다. 상태는 `reference_values_within_limits`, `reference_exceedance`, `insufficient_reference_data`다. 모든 결과의 `admission=not_assessed`, `safe_draft_m=null`, `reference_only=true`를 유지한다. 초과도 현행 입항 불가 확정이 아니라 두 출처 값의 비교다. 과거 흘수·조차·항로 수심으로 현재 허용 흘수를 만들지 않는다.
- 현재 흘수·정확한 LOA·선석/항로·입항 예정 시각의 조위/기상·UKC·화물/도선 조건·최신 항만 공지와 항만 당국 확인이 필요하다. 수집일은 자료의 유효 시점과 다르다. 이 파일은 앱에서 자동 호출되지 않고 LLM 라우팅에도 연결되지 않는다.

실행 위치는 저장소 최상위다. 아래 경로는 준비한 로컬 파일로 바꾼다. 결과는 git 제외 `output/`에 저장하고 원본은 덮어쓰지 않는다.

```bash
uv run --project backend python -m scripts.prepare_port_references prepare --vessels /path/to/vessels.csv --marine /path/to/marine_vessels.csv --wpi /path/to/UpdatedPub150.csv --output output/port-linkage --fetch-wikidata --sample-size 50
```

표본은 기본적으로 MarineVessels와 유일하게 연결된 IMO의 정렬 순서 앞 50개다. `--discovery-json`을 주면 그 응답의 IMO와 교집합을 구해 앞 50개를 선택한다. 선택된 전체 IMO는 manifest에 보존한다. 수집당 1~100척으로 제한한다. 전체 수량 명제는 LIMIT 없이 조회하며 응답은 8MiB 이하만 허용한다.

오프라인 재실행은 동일 명령에서 `--fetch-wikidata`를 빼고 기존 `output/port-linkage/raw/`를 사용한다. 명시적으로 다시 수집하면 snapshot을 교체하므로 과거 수집본을 유지하려면 별도 출력 폴더를 쓴다.

```bash
uv run --project backend python -m scripts.prepare_port_references compare --data-dir output/port-linkage --imo 9013000 --port-id WPI:60400
```

예시는 해당 IMO가 표본에 포함된 경우의 참조 비교다. 항구별 서비스 조회·주변 항구 거리 계산·실제 방문 항구 추정은 이번 시범에 포함하지 않는다. WPI 필드 의미는 [NGA 원문 설명서](https://msi.nga.mil/api/publications/download?key=16920959/SFH00000/WPI_Explanation_of_Data_Fields.pdf&type=view)를 따른다.
