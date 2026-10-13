# 0013. 제원 후보를 별도 보존하고 MRV 참고 집약도를 별도 API로 제공한다

- 상태: 제안 - 로컬 구현 기준, 팀 검토 전
- 날짜: 2026-10-13
- 결정자: 역할4 제안
- 관련: [계획](../plans/2026-10-13-mrv-reference-intensity.md), [Tool 계약](../specs/tools.md)

## 배경

실제 MRV 선박의 63.22%에 DWT 후보가 있으나 이는 CII 입력 확보율이 아니다. 보고 CO₂·역산 거리와 함께 참고 산술에는 쓸 수 있지만 DCS 전 세계 연간 범위·시점별 제원·보정·법정 검증은 충족하지 않는다.

## 결정

- `maritime_data.capacity_candidates`에 제원 후보·출처별 값·상태·원본 CSV SHA256·논리 행·원본 메타데이터를 별도 적재한다. 기존 annual_reports와 vessels의 값은 바꾸지 않는다.
- 참조 적재는 별도 CLI의 미리보기/명시 apply로 수행하며 migration 이력·REAL IMO 연결·빈 참조 테이블을 확인하고 한 트랜잭션으로 적재한다. 기존 원본·제원 후보를 덮어쓰지 않는다.
- `/api/maritime-data/reference-intensity`를 별도 제공한다. 기존 `/calculate`의 blocked 의미·공식 미산출 계약은 유지한다.
- 단일 승인 MRV 보고와 양수 CO₂·역산 거리·DWT 후보가 있고, 지원 선종(Bulk carrier/Container ship/Oil tanker)이며 제원 충돌이 없는 경우에만 `estimated` 참고값을 반환한다. 연료 내역으로 CO₂를 재계산하지 않는다.
- 참고값은 항상 비공식·검토 필요이고 DCS 범위·거리 범위·제원 시점 미검증을 표시한다. 공식 CII·등급은 null, compliance는 not_assessed다. 입력에서 한계 수용을 명시해야 한다.

## 검토한 대안

| 대안 | 선택하지 않은 이유 |
|---|---|
| 원본 MRV의 DWT를 후보로 채움 | 원본 값과 외부 참고값의 의미·출처 혼합 |
| 기존 CO₂ calculate 응답에 참고값을 자동 추가 | blocked·공식 입력 부족의 기존 계약을 오해할 수 있음 |
| 후보 CSV를 매 API 요청에서 읽음 | 배포 경로·동시 변경·재현성 관리가 어려움 |

## 결과

공개 후보를 활용하되 원본과 공식 산정 경계를 유지한다. 참조 테이블·원본 CSV는 앱 JSON 백업 범위 밖이며 PostgreSQL/파일 백업 대상이다. 참조 버전 교체는 별도 운영 검토가 필요하고 암묵적 upsert는 제공하지 않는다.

- 번호 0012는 팀 PR #11의 LLM 공급자 ADR이 사용하므로 충돌을 피하기 위해 0013으로 지정했다.
