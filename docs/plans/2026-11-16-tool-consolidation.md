# Tool 등록·스키마·실행 코드 통합

- 상태: 완료 (구현·검증, [PR #31](https://github.com/dudco/sea-the-answer/pull/31) 제출)
- 작성: 손태호 · Codex 지원
- 기준: 최초 팀 main `276a8bf` (PR #30 포함), 2026-11-18에 `02d08ce` (PR #32 포함) 통합
- 관련: [아키텍처](../architecture.md), [통합 답변](../specs/integrated-answer.md), [업무 스킬](../specs/workflow-skills.md), [ADR 0024](../adr/0024-llm-tool-selection.md)

## 목적

도구 목록·입력 모델·실행 코드가 `api/tool_registry.py`, `api/tool_dispatch.py`, `api/workflow_skills.py`, `api/store/tools.py`, `tools/`에 나뉘어 있다. 실행 중 사용하는 Tool을 기존 `backend/tools/`에서 찾을 수 있게 모으고, 모델용 JSON·HTTP 계산 목록·업무 스킬 목록을 공통 정의에서 제공한다. API는 인증·권한 경계와 실행 기록 저장을 유지한다.

## 범위와 유지할 계약

- 포함: 공통 입력 스키마·등록부, 기존 실행기 이동과 import 수정, 계산과 DB 기록 분리, 카탈로그 중복 제거, 회귀 검증과 경로 문서 갱신.
- 기존 HTTP URL·요청 필드·응답·오류·인용·소유권 검증·참고값/실제 자료 구분은 유지한다. 모델에 도구를 추가 노출하지 않는다.
- LLM 공급자 호출과 질문 오케스트레이션은 `api/`에 남긴다. 전처리·다운로드는 `scripts/`, DDL은 `migrations/`에 남긴다.
- 기존 main과 분리한 `codex/tool-consolidation` 작업 브랜치의 통합 변경을 PR #31로 제출한다. 개인 Qwen 연결과 응답 평가에서 도출한 개선안은 이 PR에 포함하지 않는다.

## 단계

- [x] 최신 main·관련 지침·공개 계약·분산 위치 확인.
- [x] Tool 입력과 카탈로그를 공통 정의로 정리.
- [x] 기존 실행기와 업무 스킬을 `tools/`로 이동하고 API 어댑터 연결.
- [x] 계산 함수와 `tool_runs` 저장 분리.
- [x] 공통 정의와 실제 실행의 일치·기존 권한·질문/계산 회귀 검증.
- [x] Docker E2E 검사 및 문서 경로 갱신.
- [x] 개인 실행 설정을 제외하고 통합 코드·호환성 검사·구조 문서를 PR 범위로 분리.
- [x] PR #32의 선박 별칭·날짜별 기록 위치·소유권을 확인한 후속 문맥 처리를 보존하며 최신 main의 충돌 해결.

## 완료 기준

모델용 13개 항목과 업무 스킬 6개 및 기존 계산기 2개의 공개 계약을 보존한다. 도구 정의와 구현의 위치가 명확하고 기존 앱의 조회·계산·문서 범위·이력 처리가 회귀하지 않아야 한다. 미실행 검사는 성공으로 기록하지 않는다.

## 검증 기록

- 2026-11-18: 최신 main `02d08ce`와의 두 충돌을 해결했다. 모델 JSON·공개 카탈로그·HTTP 계산 목록·업무 스킬 목록 및 계산 표본 3건은 최신 main과 동일하다. 선박·날짜·후속 문맥 관련 실행기 13개 함수도 동일함을 확인했다.
- 2026-11-18: 캐시된 고정 의존성 Python 3.13 이미지 + 별도 임시 PostgreSQL 18에서 backend 전체 **1,334 passed / 105 skipped**, 475.69초. 첫 화면 검사 **38 passed / 1 failed**의 복원 요청은 서버 200 완료였으며, 15초 검사 대기를 실제 앱의 32초 요청 제한을 관찰할 수 있는 35초로 맞췄다. BUSY 제한과 결과 검증은 유지한다. 다운로드 장애로 새 로컬 production 빌드는 확인하지 않았으며 캐시 이미지에 현재 소스를 읽기 전용 연결했다.
- 2026-11-18: 새 임시 DB·검색 스택에서 해당 `workspace.spec.js` 재검사 **1 passed / 2.6분**. 앞선 38개 통과와 별도 실행으로 기록한다. 전체 GitHub 빌드·화면 검사는 [PR #31 체크](https://github.com/dudco/sea-the-answer/pull/31/checks)에서 확인한다.
- 2026-11-16: Docker dev 이미지의 `uv sync --locked` 환경(Python 3.13, pytest 9.1.1)에서 기본 backend 전체 **1,146 passed / 205 skipped**, 126.60초. 서비스가 필요한 검사는 아래 opt-in 검사로 별도 확인한다.
- 기존 main의 모델용 JSON·모델 카탈로그·HTTP 계산 목록·업무 스킬 목록이 이동 후에도 동일하다. 계산기 표본 3건도 실행 ID를 제외한 결과가 동일하다.
- Docker E2E **39 passed / 4.0분**. 기존 UI 계약과 달라진 검사(답변 방식 제거·본문 우선 표시·중첩 상세 보기)를 보완한 뒤, 임시 DB와 검색 서비스를 모두 재생성해 통과했다. 모델 미설정 E2E는 `legacy` 경로이며 실제 Qwen 시험과 구분한다.
- 일회용 PostgreSQL의 기존 경로 검사는 `HAEDAP_QUESTION_ROUTER=legacy`를 명시해 **130 passed / 4 skipped**. 모의 플래너와 실제 HTTP·PostgreSQL을 사용한 새 모델 경로의 소유권 저장·계정 해제 시 기록 롤백 **2 passed**. 검사 범위는 겹치므로 합산하지 않는다.
