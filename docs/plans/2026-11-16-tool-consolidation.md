# Tool 등록·스키마·실행 코드 통합

- 상태: 완료 (구현·검증, 통합 변경만 Draft PR 제출 대상)
- 작성: 손태호 · Codex 지원
- 기준: 팀 main `276a8bf` (PR #30 포함)
- 관련: [아키텍처](../architecture.md), [통합 답변](../specs/integrated-answer.md), [업무 스킬](../specs/workflow-skills.md), [ADR 0024](../adr/0024-llm-tool-selection.md)

## 목적

도구 목록·입력 모델·실행 코드가 `api/tool_registry.py`, `api/tool_dispatch.py`, `api/workflow_skills.py`, `api/store/tools.py`, `tools/`에 나뉘어 있다. 실행 중 사용하는 Tool을 기존 `backend/tools/`에서 찾을 수 있게 모으고, 모델용 JSON·HTTP 계산 목록·업무 스킬 목록을 공통 정의에서 제공한다. API는 인증·권한 경계와 실행 기록 저장을 유지한다.

## 범위와 유지할 계약

- 포함: 공통 입력 스키마·등록부, 기존 실행기 이동과 import 수정, 계산과 DB 기록 분리, 카탈로그 중복 제거, 회귀 검증과 경로 문서 갱신.
- 기존 HTTP URL·요청 필드·응답·오류·인용·소유권 검증·참고값/실제 자료 구분은 유지한다. 모델에 도구를 추가 노출하지 않는다.
- LLM 공급자 호출과 질문 오케스트레이션은 `api/`에 남긴다. 전처리·다운로드는 `scripts/`, DDL은 `migrations/`에 남긴다.
- 기존 main과 분리한 `codex/tool-consolidation` 작업 브랜치의 통합 변경을 독립된 Draft PR로 제출한다.

## 단계

- [x] 최신 main·관련 지침·공개 계약·분산 위치 확인.
- [x] Tool 입력과 카탈로그를 공통 정의로 정리.
- [x] 기존 실행기와 업무 스킬을 `tools/`로 이동하고 API 어댑터 연결.
- [x] 계산 함수와 `tool_runs` 저장 분리.
- [x] 공통 정의와 실제 실행의 일치·기존 권한·질문/계산 회귀 검증.
- [x] Docker E2E 검사 및 문서 경로 갱신.
- [x] 개인 실행 설정을 제외하고 통합 코드·호환성 검사·구조 문서를 PR 범위로 분리.

## 완료 기준

모델용 13개 항목과 업무 스킬 6개 및 기존 계산기 2개의 공개 계약을 보존한다. 도구 정의와 구현의 위치가 명확하고 기존 앱의 조회·계산·문서 범위·이력 처리가 회귀하지 않아야 한다. 미실행 검사는 성공으로 기록하지 않는다.

## 검증 기록

- 2026-11-16: Docker dev 이미지의 `uv sync --locked` 환경(Python 3.13, pytest 9.1.1)에서 기본 backend 전체 **1,146 passed / 205 skipped**, 126.60초. 서비스가 필요한 검사는 아래 opt-in 검사로 별도 확인한다.
- 기존 main의 모델용 JSON·모델 카탈로그·HTTP 계산 목록·업무 스킬 목록이 이동 후에도 동일하다. 계산기 표본 3건도 실행 ID를 제외한 결과가 동일하다.
- Docker E2E **39 passed / 4.0분**. 기존 UI 계약과 달라진 검사(답변 방식 제거·본문 우선 표시·중첩 상세 보기)를 보완한 뒤, 임시 DB와 검색 서비스를 모두 재생성해 통과했다. 모델 미설정 E2E는 `legacy` 경로이며 실제 Qwen 시험과 구분한다.
- 일회용 PostgreSQL의 기존 경로 검사는 `HAEDAP_QUESTION_ROUTER=legacy`를 명시해 **130 passed / 4 skipped**. 모의 플래너와 실제 HTTP·PostgreSQL을 사용한 새 모델 경로의 소유권 저장·계정 해제 시 기록 롤백 **2 passed**. 검사 범위는 겹치므로 합산하지 않는다.
