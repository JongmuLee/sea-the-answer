# 아키텍처

> 대상 버전: 1.4.0 · 최종 확인: 2026-10-13 (코드 대조)
> 이 문서는 **현재 구현**을 설명합니다. 앞으로 바꿀 계획은 [`plans/`](plans/), 바뀌지 않을 결정의 이유는 [`adr/`](adr/)에 있습니다.

## 1. 한눈에 보기

해답은 서비스 세 개(+ 시작 때 한 번 도는 마이그레이션)로 이루어진 웹 애플리케이션입니다. 사용자는 **Next.js 주소 하나**(기본 `http://localhost:5173`)로만 접속합니다. Next.js가 화면을 그리고, `/api/*` 요청은 `proxy.js`가 FastAPI 백엔드로 넘깁니다([ADR 0006](adr/0006-nextjs-entry-rewrites-to-api.md), [ADR 0007](adr/0007-runtime-api-origin-via-proxy.md)). 백엔드만 PostgreSQL에 접근합니다.

`frontend/`(npm)와 `backend/`(uv)는 각각 독립된 패키지이고, 저장소 최상위의 `compose.yaml`이 셋을 함께 띄웁니다. 최상위 `e2e/`는 실행 중인 스택을 브라우저로 검증합니다([ADR 0009](adr/0009-split-packages-and-compose.md)).

```mermaid
flowchart LR
  B[브라우저<br/>React UI] -->|HTTP :5173| N
  subgraph C[docker compose · 프로젝트 haedap]
    N[frontend<br/>Next.js :5173<br/>유일하게 공개되는 포트]
    G[backend<br/>FastAPI :8000<br/>api.main:app]
    D[(db<br/>PostgreSQL 18<br/>볼륨 db-data)]
    M[migrate<br/>dbmate · migrations/<br/>시작 때 1회]
    K[/백업 파일<br/>볼륨 backups/]
    M -->|대기 중인 마이그레이션 적용| D
    N -->|/api/* proxy.js<br/>X-Forwarded-Host| G
    G --> D
    G --> K
  end
  G -. 선택: OPENAI_API_KEY 설정 시 .-> O[(외부 LLM API)]
  E[e2e<br/>Playwright] -. profile e2e .-> N
```

| 서비스 | 이미지(빌드 위치) | 포트 | 데이터 |
|---|---|---|---|
| `db` | `postgres:18-alpine` | 내부 5432 (개발 오버레이에서만 `127.0.0.1:5432` 공개) | 볼륨 `db-data` |
| `migrate` | `amacneil/dbmate:2.36.0` | 없음 | 최상위 `migrations/` 마운트. `up` 후 종료([ADR 0010](adr/0010-dbmate-migrations.md)) |
| `backend` | `backend/Dockerfile` (`prod`/`dev` 타깃) | 내부 8000 (개발 오버레이에서만 `127.0.0.1:8000` 공개) | 볼륨 `backups` → `/data/backups` |
| `frontend` | `frontend/Dockerfile` (`prod`=standalone, `dev`=`next dev`) | `${HAEDAP_BIND:-127.0.0.1}:${PORT:-5173}` | 없음 |
| `e2e-*`, `e2e` | 같은 이미지·Dockerfile + `e2e/Dockerfile` | 공개 안 함 | DB·백업 모두 tmpfs (실행마다 빈 상태, `e2e-migrate`로 같은 마이그레이션 적용) |

- **운영 실행**: `docker compose up --build -d`. 시작 순서는 db(헬스체크 통과) → migrate(성공 종료) → backend(헬스체크 통과) → frontend입니다. 마이그레이션이 실패하면 backend가 시작하지 않습니다. backend 헬스체크는 `/api/health`를 호출하므로 DB 연결까지 확인합니다.
- **개발 실행**: `docker compose -f compose.yaml -f compose.dev.yaml up --build`. 소스를 컨테이너에 바인드 마운트하고 `uvicorn --reload`, `next dev`로 저장 즉시 반영합니다. Docker 없이 `backend/`에서 `uv run`, `frontend/`에서 `npm run dev`로 직접 실행하는 방법도 유지합니다([ADR 0005](adr/0005-dev-mode-direct-processes.md)).
- **API 주소**: `HAEDAP_API_ORIGIN`. compose에서는 `http://backend:8000`, 직접 실행에서는 기본 `http://127.0.0.1:8000`. `proxy.js`가 **요청마다** 이 값을 읽으므로 바꿔도 다시 빌드할 필요가 없습니다. 직접 실행할 때는 `next.config.mjs`가 이 값 하나만 최상위 `.env`에서 읽습니다.
- `next.config.mjs`는 `output: 'standalone'`으로 빌드하고, `next dev`는 `.next-dev`, 그 외에는 `.next`를 출력 폴더로 씁니다(`HAEDAP_NEXT_DIST`로 덮어쓰기 가능).
- 외부 네트워크는 선택적 LLM 호출에서만 사용합니다.

## 2. 요청 흐름

### 2.1 Next.js → FastAPI (`frontend/src/proxy.js`, `backend/api/web/handler.py`)

1. 브라우저 요청은 모두 Next.js가 받습니다. 화면·`/_next/*`·`frontend/public` 리소스는 Next.js가 직접 응답합니다.
2. `/api/:path*`는 `proxy.js`(Next.js 16의 `middleware` 후속, Node.js 런타임)가 `NextResponse.rewrite`로 `HAEDAP_API_ORIGIN/api/:path*`에 프록시합니다. 원래 `Host`는 `X-Forwarded-Host`에 들어갑니다. 쿠키·`Set-Cookie`는 그대로 오갑니다.
3. 프록시 설정(`experimental`): 요청 본문 최대 **101MB**(PDF 포함 문서 등록 38MB, 백업 가져오기 100MB), 응답 대기 **60초**.
4. FastAPI의 `/api/{path}` 하나가 모든 앱 API를 받습니다. 요청마다 PostgreSQL 연결을 열고(`Workspace`), 첫 요청에서 `bootstrap()`(관리자 계정·기본 설정·`knowledge/seed.json` 문서 등록)을 한 번 실행합니다.
5. **Host·Origin 검사**(`validate_origin`): `X-Forwarded-Host`(없으면 `Host`)의 호스트 이름이 `localhost`, `127.0.0.1`, `HAEDAP_ALLOWED_HOSTS`에 있어야 하고, `Origin`이 있으면 같은 주소여야 합니다. 아니면 403.
6. 동시 요청 4개 제한, 백업 복구 중 다른 요청 차단(PostgreSQL advisory lock), 1분마다 자동 백업 여부 확인.
7. `MARITIME_DATA_API_TOKEN`이 설정되어 있으면 `/api/maritime-data/*` 라우터가 `/api/{path}`보다 먼저 등록됩니다(8절).

### 2.2 화면 (`frontend/`)

- `src/app/` — App Router 페이지. `/`는 `/chat`으로 리다이렉트. 페이지: `/chat`, `/documents`, `/documents/[id]`, `/operations`, `/reports`, `/reports/[id]`, `/admin`.
- `src/workspace/provider.jsx` — `WorkspaceProvider`가 작업공간 상태를 소유하고 컴포넌트는 `useSyncExternalStore`로 구독.
- `src/workspace/controller.jsx` — 사용자 이벤트 처리, API 호출, 대화상자, 초안 보존.
- `src/lib/api.js` — 유일한 서버 호출 지점. `X-Haedap-Token`(CSRF), `X-Haedap-Identity`(현재 사용자 확인) 헤더를 붙임.
- `src/lib/pdf.js` — 브라우저 안의 PDF.js로 텍스트 추출(업로드 PDF는 외부로 보내지 않음). `src/lib/csv.js` — 운항 CSV 파싱.
- `src/lib/model.js` — 클라이언트 상태 모델, 미저장 초안의 `localStorage` 보관.

### 2.3 통합 질문 (`POST /api/ask`)

```mermaid
sequenceDiagram
  participant UI as 브라우저
  participant API as routes/documents.ask
  participant WS as Workspace.answer
  participant DB as PostgreSQL
  UI->>API: question, task, context{ship,from,to}, criterion
  API->>WS: 사용자·CSRF 확인 후 호출
  WS->>DB: search() — 적용 중 문서의 청크 중 열람 가능한 것
  WS->>WS: 질문 단어(+한영 동의어)가 포함된 개수로 순위 → 상위 5
  alt mode=llm 이고 모델 설정됨
    WS->>WS: 외부 LLM → 인용 구절이 원문에 있는지 검사
  else
    WS->>WS: 상위 3개 원문 발췌
  end
  WS->>DB: 운항 필요 시 operation_summary(ship, from, to)
  WS->>WS: criterion 있으면 단일 기준 수치 비교
  WS->>DB: queries + query_owner + audit 기록
  WS-->>UI: statements, evidence, operations, compliance, ...
```

응답 형식은 [`specs/integrated-answer.md`](specs/integrated-answer.md)를 따릅니다.

## 3. 모듈 책임 (`backend/`)

| 경로 | 책임 |
|---|---|
| `api/main.py` | FastAPI 앱 생성, lifespan(자동 백업 루프), 해사 데이터 라우터 마운트, 오류 응답 형식 |
| `api/web/handler.py` | `/api/{path}` 진입점: 요청 슬롯, Content-Type·본문 크기, DB 세션, 인증·CSRF 후 `routes.dispatch` |
| `api/web/security.py` | Host·Origin 검사, 게스트 쿠키, 관리자 세션(메모리), CSRF 토큰, 로그인 시도 기록 |
| `api/web/concurrency.py` | 동시 요청 4개 제한, 복구 단독 실행(프로세스 카운터 + PostgreSQL advisory lock) |
| `api/web/backup_job.py` | 1분마다 확인하는 하루 1회 자동 백업 |
| `api/routes/` | 경로 표와 도메인별 핸들러: `system`(health·tools), `auth`, `documents`(문서·PDF·ask·search·history), `operations`, `changes`, `reports`, `admin`(users·logs·backups) |
| `api/store/` | `Workspace` = `base.Database`(연결·트랜잭션) + 도메인 mixin: `users`, `documents`(청크 900자·검색), `operations`, `changes`, `reports`, `answers`(통합 답변·선택적 LLM), `tools`, `backups` |
| `api/config.py`, `api/deps.py` | 환경 설정(DB 주소, 백업 경로, 허용 호스트), `Workspace` 열기와 최초 1회 초기화 |
| `api/errors.py`, `api/validation.py` | `ApiError`·`require`, 입력 검증·형식 도우미 |
| `knowledge/seed.json`, `knowledge/sample-data.json` | 기본 문서, "예시 데이터 넣기"용 가상 운항 기록 |
| `tools/maritime_data/` | 서비스 실행 중 사용하는 읽기 전용 해사 데이터 조회·계산·규칙 기반 조회 도우미(8절) |
| `tests/` | 앱과 해사 Tool pytest (`backend/`에서 `uv run pytest`) |

최상위 `scripts/maritime_data/`는 원본·공개 참조 자료 정규화, 전처리·품질 판정, 필드 사전 생성, CSV 적재 및 명시적 선박 연결을 담당합니다. backend uv 환경의 `data` 의존성 그룹을 사용하며 운영 이미지에는 포함하지 않습니다([ADR 0011 제안](adr/0011-maritime-backend-integration.md)). 자료 준비는 서비스 시작이나 Tool 호출 때 자동 실행하지 않습니다.

DB 스키마는 backend가 아니라 최상위 `migrations/`(dbmate)가 소유합니다. backend는 테이블이 없으면 `SCHEMA_NOT_READY`(503)를 반환합니다.

`frontend/tests/`는 CSV·PDF 유틸 단위 테스트(`npm test`), 최상위 `e2e/tests/`는 화면 → API → DB 전체 흐름 검증(Playwright)입니다.

## 4. 보안 경계

모듈을 교체하더라도 아래 경계는 유지해야 합니다.

- **접속 범위**: 공개되는 포트는 frontend 하나입니다. 기본 바인딩은 `127.0.0.1`(이 PC만). `HAEDAP_BIND=0.0.0.0`으로 LAN·서버 접속을 열고, 접속에 쓰는 호스트 이름·IP를 `HAEDAP_ALLOWED_HOSTS`에 넣습니다. TLS는 포함하지 않으므로 인터넷 공개 시 앞단에 리버스 프록시(TLS)가 필요합니다.
- **내부 API·DB**: backend와 db는 compose 내부 네트워크에만 있고 운영 구성에서는 호스트에 공개하지 않습니다(개발 오버레이만 `127.0.0.1`로 공개). backend는 compose 네트워크 안에서만 접근되므로 로그인 시도 제한을 위해 `X-Forwarded-For`를 신뢰합니다(`--forwarded-allow-ips *`).
- **접속자 IP 범위**: 백엔드는 접속자 IP·서브넷을 검사하지 않습니다. 같은 서브넷으로 제한하려면 OS 방화벽을 씁니다(Ubuntu: `ufw allow from 192.168.0.0/24 to any port 5173`).
- **단일 출처**: 브라우저는 Next.js 주소 하나만 사용. Origin 검사는 실제 호스트(`X-Forwarded-Host`) 기준.
- **인증**: 관리자만 로그인(salt + scrypt, HttpOnly·SameSite=Strict 세션 쿠키, 12시간, 서버 메모리 보관 — backend 재시작 시 로그아웃). 일반 사용자는 무작위 `haedap_guest` 쿠키로 작업공간만 구분(인증 수단 아님).
- **쓰기 보호**: 모든 POST는 JSON + `X-Haedap-Token` 필요. 원본 자료(문서·선박·운항) 변경은 서버에서 관리자 여부를 검사.
- **열람 범위**: 문서 `scope`(all/operator/admin)를 **검색 단계부터** 적용하고, 원본 PDF·보고서 근거·질문 이력 재열람에도 다시 검사.
- **LLM 출력 검증**: 외부 모델은 근거 청크 ID와 원문 인용 구절을 반환해야 하며, 인용이 원문에 없으면 폐기하고 원문 발췌로 되돌림.
- **근거 없는 수치 금지**: 공식 CII와 규정 적용 판정은 산출하지 않고 `unavailable` / `unknown`과 사유를 반환.
- **비밀값**: `.env`(최상위)는 Git에서 제외. API 키는 backend 환경변수로만 전달하고 frontend 컨테이너에는 넣지 않습니다.

## 5. 교체 지점 (팀 모듈 연결)

| 바꾸려는 것 | 시작 위치 | 지켜야 할 계약 |
|---|---|---|
| 검색(벡터·하이브리드 RAG) | `api/store/documents.py`의 `search`, `save_document`의 청크 분할 | 열람 범위 필터 유지, evidence 필드 형식 |
| 답변 생성(LLM 공급자) | `api/store/answers.py`의 `grounded_statements`, `answer`의 `mode=llm` 분기 | statements의 `chunkId`·`quote` 인용 검증 |
| 의도 분류·Agent | `api/store/answers.py`의 `answer` | [`specs/integrated-answer.md`](specs/integrated-answer.md) 출력 형식 |
| 계산 Tool 추가 | `api/store/tools.py`(`TOOL_DEFINITIONS`, 계산 메서드), `api/routes/system.py`(경로) | 입력 검증, `version`·`assumptions` 반환, `tool_runs` 기록 |
| 보고서 서식 | `api/store/reports.py`의 `generate_report` | 없는 값은 `[미입력 · 확인 필요]` |
| 새 서비스(로컬 LLM, 벡터 DB 등) | `compose.yaml`에 서비스 추가, backend 환경변수로 주소 전달 | 공개 포트는 frontend 하나 유지 |

연결 순서와 담당은 [`plans/2026-10-08-team-module-integration.md`](plans/2026-10-08-team-module-integration.md)를 참고하세요.

## 6. 빌드·실행 산출물

| 경로 | 생성 시점 | Git |
|---|---|---|
| `frontend/node_modules/`, `e2e/node_modules/` | `npm ci` (각 폴더) | 제외 |
| `backend/.venv/` | `uv sync` (`backend/`) | 제외 |
| `frontend/.next/`, `frontend/.next-dev/` | `npm run build`, `npm run dev` | 제외 |
| 볼륨 `haedap_db-data`, `haedap_backups` | `docker compose up` | — (`docker compose down -v`로 삭제됨) |
| `backend/data/` | 직접 실행 시 백업(`data/backups/`) | 제외 |
| `e2e/test-results/`, `e2e/playwright-report/` | e2e 실행 | 제외 |
| `schema_migrations` 테이블 | dbmate (적용한 마이그레이션 버전) | — |
| `.env` | 사용자가 직접 | 제외 |

## 7. 알려진 한계

- 문서 청크를 요청마다 전부 읽어 메모리에서 순위를 매기는 캡스톤 데모 규모 설계. 대규모 운영에는 인덱스·페이징·파일 저장소·TLS·로그 보존 정책이 필요.
- 검색은 단어 포함 여부 기반(이전 Node 구현의 FTS5·BM25·한글 바이그램보다 단순)이라 의미가 같고 단어가 다른 질문, 교차 언어 질문에 약함.
- 관리자 세션은 backend 프로세스 메모리에 있어 backend를 여러 개 띄우면 공유되지 않음.
- 질문 유형 분기는 단어 규칙 기반. 질문 속 날짜·선박명 자동 추출은 미구현.

## 8. 해사 데이터 Tool의 경계 (선택)

`backend/tools/maritime_data/`는 PostgreSQL `maritime_data` 스키마의 승인 View를 읽기 전용으로 조회·계산합니다. backend와 같은 uv 프로젝트이며, `MARITIME_DATA_API_TOKEN`(32자 이상)을 설정하면 같은 FastAPI 프로세스에 `/api/maritime-data/*`로 마운트됩니다. 설정하지 않으면 라우트가 없습니다. 기본은 앱의 `DATABASE_URL`이며 `MARITIME_DATA_DATABASE_URL`로 별도 DB를 선택할 수 있습니다. 별도 DB에도 기본 init과 해사 마이그레이션을 모두 적용해야 하며 선박 외래키는 그 DB 안의 `public.ships`만 참조합니다.

```mermaid
flowchart LR
  R[별도 원본·참조 자료] --> P[scripts/maritime_data<br/>오프라인 전처리·품질·출처 보존]
  M[migrations/<br/>dbmate · DDL만 적용] --> S[(PostgreSQL 앱 테이블<br/>public.ships / operations)]
  M --> DB[(같은 DB · maritime_data<br/>MRV / Noon / 출처 / 품질)]
  P -->|migration 선행·빈 테이블 CSV 적재| DB
  B[브라우저] --> N[Next.js :5173]
  N -->|/api/* proxy.js| G[FastAPI backend]
  G --> S
  DB -. vessels.ship_id<br/>검토한 명시적 연결 .-> S
  G -->|/api/maritime-data/*<br/>Bearer 토큰 필요| T[tools.maritime_data 라우터]
  T -->|승인 View 읽기 전용| DB
```

`vessels.ship_id`의 선택적 외래키로 기존 앱 `ships`를 재사용합니다. 연결은 준비 CLI의 명시적 매핑으로만 수행하고 외부 선박 전체를 자동 등록하지 않습니다. MRV는 보고기간 단위이므로 기존 일별 `operations`로 분해하지 않습니다. 원본 식별자와 REAL/SYN 구분, 출처·품질 정보를 그대로 유지합니다.

라우트는 서버 전용 Bearer 토큰을 요구하므로 **화면에서 바로 호출할 수는 없습니다.** 화면 연결은 아직 구현되지 않았고, 연결할 때는 토큰을 숨긴 채 팀 접근 정책·POST CSRF·사용자별 감사·오류 처리를 적용하는 어댑터가 필요합니다. DB에 선박 연결 컬럼을 추가한 것만으로 화면의 앱 선박 ID가 Tool의 원본 식별자로 자동 변환되지는 않습니다. 공식 CII 등급은 생성하지 않으며, `/ask`의 실제 LLM 호출은 비활성입니다.

`tools.maritime_data.api.create_app()`은 테스트·역할4 도구용 독립 앱으로 남아 있습니다. 마이그레이션은 앱과 해사 DDL을 소유하고 loader는 DDL을 실행하지 않습니다. 기존 단일 스키마 채택·rollback 제한과 앱 복구 시 선박 연결 정책은 [데이터 모델](data-model.md#9-postgresql-해사-데이터-tool-선택)에 정의합니다. 입출력은 [API 명세](specs/maritime-data-api.md), 데이터 확보는 [운영 안내](user-guide.md#10-해사-데이터-tool-선택)를 참고하세요.
