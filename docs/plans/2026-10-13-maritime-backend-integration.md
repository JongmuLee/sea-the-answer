# 해사 데이터 Tool·마이그레이션·기존 선박 모델 통합

- 상태: 진행 중
- 작성: 2026-10-13 · 역할4
- 담당: 손태호 (역할4) · 팀 리뷰 후 통합
- 관련: [ADR 0011 제안](../adr/0011-maritime-backend-integration.md), [데이터 모델](../data-model.md#9-postgresql-해사-데이터-tool-선택), [API 계약](../specs/maritime-data-api.md), [데이터 계약](../specs/maritime-data.md)

## 목표와 범위

팀 피드백을 반영해 runtime Tool을 `backend/tools`로, 오프라인 자료 준비를 `scripts`로 나누고 DDL은 루트 `migrations`로 합친다. 기존 앱 `ships`는 명시적 선박 매핑으로 재사용한다. MRV 보고기간·합성 Noon·출처·품질은 별도 모델로 보존한다. 사용자 데이터의 재적재, 대량 앱 선박 생성, 공식 CII 계산, 화면 어댑터와 외부 모델 연결은 이번 범위가 아니다.

## 단계와 완료 조건

- [x] 기준 브랜치와 팀 피드백을 확인하고 API·데이터 의미를 보존하는 통합 경계 문서화
  - 완료 조건: ADR 제안, 계획, 기존 문서와 새 폴더 허용 규칙의 연결
- [x] 실행·준비 모듈 분리 및 import·Docker·테스트 경로 갱신
  - 완료 조건: runtime 이미지에 오프라인 스크립트가 없고 backend 테스트에서 새 모듈 경로 사용
- [x] 새 dbmate 마이그레이션과 안전한 기존 스키마 채택
  - 완료 조건: init 파일 불변, fresh/legacy/conflict/up/down·데이터 보존 시나리오 검증
- [x] DDL 없는 빈 테이블 CSV 적재와 명시적 `vessels.ship_id` 매핑
  - 완료 조건: 비어 있지 않은 대상 거절, 트랜잭션 롤백, 잘못된 매핑 거절 및 앱 선박 자동 생성 없음
- [x] 앱 백업 복구 시 외부 선박 연결 안전 처리
  - 완료 조건: 동일 정체성만 재연결, 삭제·불일치는 NULL 및 처리 건수 기록, 해사 원본은 JSON 백업 제외
- [x] 관련 문서·의존성·예제와 검증 기록 정합성 확인
  - 완료 조건: 현재 코드 경로와 명령 대조, 실행한 결과만 changelog에 기록
- [ ] PR 제출
  - 완료 조건: diff·민감정보·테스트·미검증 범위 검토 후 팀 기준 브랜치에 제출

## 검증 계획

backend pytest, 준비 스크립트 테스트, 임시 PostgreSQL의 fresh/legacy/conflict migration 및 down 거절, 빈 테이블 적재·명시 매핑·복구 회귀를 확인한다. 가능하면 compose 설정·이미지 및 별도 e2e 스택도 검증한다. 도구나 네트워크 제한으로 실행하지 못한 항목은 통과로 기록하지 않는다. 실제 개인 DB·CSV 원본과 설정 파일은 변경하지 않는다.

## 영향 받는 영역

| 영역 | 변경 |
|---|---|
| `backend/tools/`, `backend/api/main.py` | 실행 Tool 경로·마운트 import |
| `scripts/maritime_data/` | 오프라인 준비·적재·사전·설정 보조·선박 매핑 |
| `migrations/` | 새 해사 DDL·기존 스키마 채택·선박 FK |
| `backend/api/store/backups.py` | 앱 복구 시 현재 해사 선박 연결 보존·검증 |
| backend 의존성·Docker·테스트 | runtime과 data 그룹·경로 정합성 |
| README·AGENTS·docs | 새 구조·실행·데이터/API 계약·계획·ADR·검증 기록 |

## 위험과 대응

| 위험 | 대응 |
|---|---|
| 기존 데이터가 있는 스키마를 fresh DB로 오인 | 단일 기존 스키마 구조 검증·채택, 양쪽 존재/불일치 거절, loader는 빈 테이블만 |
| 마이그레이션 rollback으로 원본 삭제 | 데이터가 있는 down 거절, PostgreSQL 백업 별도 보관 |
| 앱 선박 복구가 해사 FK 또는 정체성을 훼손 | SET NULL 후 동일 정체성만 재연결, 누락·불일치 건수 기록 |
| 코드 위치 변경으로 기존 계약·보안 손실 | 조회 응답·Decimal·Bearer·CII 제약 회귀 테스트 유지 |

## 열린 질문

- 앱 사용자별 선박 선택을 Tool 원본 키로 변환하는 화면 어댑터와 접근·감사 정책은 후속 팀 연결 단계에서 확정한다.
- 별도 해사 DB가 필요한 운영 요구가 생기면 동일 DB 기본 정책과 데이터 이전 범위를 다시 검토한다.

## 진행 기록

- 2026-10-13: `refactor/split-packages` 기준에서 피드백 반영 시작. 기존 채택 ADR의 본문은 유지하고 새 ADR 0011을 제안했다. 검증 결과는 완료 후 [changelog](../changelog.md)에 기록한다.
