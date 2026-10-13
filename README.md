# 해답 — SEA THE ANSWER

현재 버전 **1.4.0** · 마지막 업데이트 **2026-10-13**

선내 문서 근거와 운항 기록을 함께 보여 주는 선박 업무 지원 앱입니다. 화면은 Next.js, `/api/*`는 Python FastAPI, 저장소는 PostgreSQL이며, 저장소 최상위에서 `docker compose` 하나로 셋을 함께 실행합니다.

| 화면 | 기능 |
|---|---|
| 통합 질문 | 문서 근거 + 선택한 선박·기간의 운항 수치·추이를 함께 답변, 근거 페이지·원문 PDF 열기, 입력 기준 비교, 보고서에 담기 |
| 문서 | 텍스트 PDF 등록·추출 검토, 메타데이터·열람 범위, 개정 버전 관리 |
| 운항 정보 | 선박·일별 기록 관리, CSV 일괄 등록, 지표 차트·선박 비교, 배출량·항해 시간 계산 |
| 보고서 | Noon / MRV 검토 초안 자동 생성, 편집·저장, 검토 요청 → 관리자 승인 |
| 관리 | 계정·역할, 승인, 질의·변경·오류 로그, 백업·복구 |

일반 기능은 로그인 없이 쓰고, 등록·수정·삭제·승인·백업은 관리자(기본 **`admin / 1234`**, 실제 사용 전에 변경)만 할 수 있습니다.

**현재 구현 범위**: 문서 검색은 질문 단어가 청크에 포함된 개수로 순위를 매기는 단순 키워드 검색이고, 답변은 기본적으로 원문 발췌입니다. 외부 LLM은 `.env`로 켜는 선택 기능입니다. 벡터·하이브리드 RAG, Local LLM, 공식 CII 계산, 규정 적용 자동 판정, DOCX·PPT 등록은 아직 연결 전입니다. 범위와 연결 계획은 [`docs/plans/2026-10-08-team-module-integration.md`](docs/plans/2026-10-08-team-module-integration.md)를 보세요.

## 프로젝트 구조

`frontend/`와 `backend/`는 각자 패키지 매니저를 가진 독립 프로젝트입니다. 최상위에는 패키지 매니저가 없고, 실행·테스트를 묶는 compose 파일만 있습니다([ADR 0009](docs/adr/0009-split-packages-and-compose.md)).

```text
sea-the-answer/
├─ compose.yaml          db + migrate + backend + frontend (운영), profile "e2e" (테스트 스택)
├─ compose.dev.yaml      개발 오버레이: 소스 마운트, 핫 리로드, DB·API 포트를 127.0.0.1에 공개
├─ .env.example          설정 예시 → .env 로 복사
├─ frontend/             Next.js 16 + React 19 — 사용자가 접속하는 유일한 진입점   [npm]
│  ├─ package.json, package-lock.json, Dockerfile, next.config.mjs
│  ├─ src/app/           페이지 라우트: /chat, /documents, /operations, /reports, /admin
│  ├─ src/views/ src/workspace/ src/lib/ src/styles/
│  ├─ src/proxy.js       /api/* 를 backend로 전달 (HAEDAP_API_ORIGIN)
│  ├─ public/            글꼴, PDF.js, Leaflet 등 오프라인 정적 리소스
│  └─ tests/             단위 테스트 (npm test)
├─ backend/              FastAPI + PostgreSQL                                       [uv]
│  ├─ pyproject.toml, uv.lock, .python-version, Dockerfile
│  ├─ api/               FastAPI 앱 — main.py(진입점), web/(보안·동시성), routes/(도메인별 경로), store/(DB·업무 로직)
│  ├─ maritime_data/     (선택) 해사 데이터 조회·계산 Tool, 전처리 스크립트
│  ├─ knowledge/         seed.json(기본 문서), sample-data.json(가상 운항 예시)
│  └─ tests/             pytest
├─ migrations/           DB 스키마 마이그레이션 (SQL, up/down)                        [dbmate]
├─ e2e/                  Playwright 브라우저 테스트 (화면 → API → DB)                [npm]
├─ docs/                 설계·명세·계획·ADR 등 모든 프로젝트 문서
└─ AGENTS.md             사람·에이전트 공통 작업 지침
```

구조와 요청 흐름은 [`docs/architecture.md`](docs/architecture.md)에 있습니다.

## 실행하기 (Docker)

준비물: **Docker Desktop**(Windows·macOS) 또는 **Docker Engine + Compose 플러그인**(Ubuntu). 첫 빌드에는 인터넷이 필요합니다. 모든 명령은 **저장소 최상위**에서 실행합니다.

1. 설정 파일을 만듭니다. 기본값으로도 이 PC에서는 바로 동작합니다. 공유 서버라면 `POSTGRES_PASSWORD`를 **처음 시작하기 전에** 바꾸세요.

   ```bash
   cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
   ```

2. 실행합니다. 처음에는 이미지 빌드로 몇 분 걸립니다. `migrate` 서비스가 DB 마이그레이션을 적용한 뒤 backend가 시작하고, DB가 비어 있으면 기본 문서와 관리자 계정이 만들어집니다.

   ```bash
   docker compose up --build -d
   ```

3. 브라우저에서 <http://localhost:5173>을 엽니다.

| 하려는 일 | 명령 |
|---|---|
| 상태 확인 (세 서비스가 `healthy`인지) | `docker compose ps` |
| 로그 보기 | `docker compose logs -f backend` (또는 `frontend`, `db`) |
| 중지 (데이터 유지) | `docker compose down` |
| 코드 변경 후 다시 띄우기 | `docker compose up --build -d` |
| **모든 데이터 삭제** 후 처음부터 | `docker compose down -v` |
| 마이그레이션 상태 보기 | `docker compose run --rm migrate status` |

데이터는 Docker 볼륨(`haedap_db-data`, 백업 파일은 `haedap_backups`)에 남습니다. 다른 PC·서버에서 접속하게 하려면 `.env`의 `HAEDAP_BIND`·`HAEDAP_ALLOWED_HOSTS`를 설정합니다([사용 안내 7절](docs/user-guide.md#7-같은-네트워크의-팀원이-접속하기--서버에-올리기)). 새 버전으로 바꾸는 방법은 [사용 안내 8절](docs/user-guide.md#8-새-버전으로-바꾸기)에 있습니다.

## 개발하기

### Docker로 (권장)

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

`frontend/`와 `backend/` 소스가 컨테이너에 마운트되어 저장하면 바로 반영됩니다(`next dev`, `uvicorn --reload`). 화면은 <http://localhost:5173>, API 문서는 <http://127.0.0.1:8000/docs>, DB는 `127.0.0.1:5432`(사용자·비밀번호는 `.env`)로 열립니다. 의존성 파일(`package-lock.json`, `uv.lock`)을 바꾸면 `--build`로 이미지를 다시 만듭니다.

### Docker 없이

PostgreSQL을 직접 준비하거나 DB만 compose로 띄웁니다(`docker compose -f compose.yaml -f compose.dev.yaml up -d db`). 최상위 `.env`에 `DATABASE_URL=postgresql://haedap:<POSTGRES_PASSWORD>@127.0.0.1:5432/haedap?sslmode=disable`을 넣습니다. 이 값 하나를 dbmate와 backend가 함께 씁니다.

```bash
# 마이그레이션 적용 (최상위에서, Node.js 필요 — dbmate가 .env의 DATABASE_URL을 읽음)
npx --yes dbmate@2.36.0 -d migrations --no-dump-schema up

# 터미널 1 — backend (Python 3.13, uv 필요: https://docs.astral.sh/uv/)
cd backend
uv sync
uv run --env-file ../.env uvicorn api.main:app --reload

# 터미널 2 — frontend (Node.js 24 + npm)
cd frontend
npm ci
npm run dev
```

새 의존성은 해당 폴더에서만 추가합니다: `cd backend && uv add <패키지>`, `cd frontend && npm install <패키지>`.

### DB 스키마 바꾸기

스키마는 최상위 `migrations/`의 SQL 파일로 관리하고 [dbmate](https://github.com/amacneil/dbmate)로 적용합니다([ADR 0010](docs/adr/0010-dbmate-migrations.md)).

```bash
docker compose run --rm migrate new add_ship_flag    # migrations/<시각>_add_ship_flag.sql 생성
# 파일의 -- migrate:up / -- migrate:down 아래에 SQL 작성
docker compose run --rm migrate up                   # 적용 (docker compose up 때도 자동 적용)
docker compose run --rm migrate rollback             # 마지막 파일 되돌리기
```

이미 커밋된 마이그레이션 파일은 고치지 말고 항상 새 파일을 추가합니다. Docker 없이 하려면 `docker compose run --rm migrate` 대신 `npx --yes dbmate@2.36.0 -d migrations --no-dump-schema`를 씁니다. Linux에서 컨테이너로 만든 파일은 root 소유일 수 있으니 필요하면 `sudo chown`으로 바꾸세요.

## 테스트

| 범위 | 위치 | 명령 |
|---|---|---|
| backend 단위·API | `backend/` | `uv run pytest` |
| frontend 단위 | `frontend/` | `npm test` |
| e2e (화면 → API → DB 전체 흐름) | 최상위 | `docker compose --profile e2e up --build --exit-code-from e2e e2e` |

e2e는 앱과 분리된 테스트 스택(`e2e-db`·`e2e-backend`·`e2e-frontend`)을 띄우고, 메모리(tmpfs) DB에서 매번 빈 상태로 시작합니다. 앱 데이터에 영향이 없고, 호스트에 포트를 열지 않으므로 앱이 실행 중이어도 됩니다. 명령의 종료 코드가 테스트 결과이며, 실패 시 스크린샷·trace는 `e2e/test-results/`, HTML 보고서는 `e2e/playwright-report/`에 생깁니다. 테스트 스택 정리: `docker compose --profile e2e down`.

Docker 없이 e2e를 돌리려면 **빈 DB로 띄운 스택**에 대고 `e2e/`에서 `npm ci`, `npx playwright install chromium`, `npx playwright test`를 실행합니다(주소는 `E2E_BASE_URL`, 기본 `http://127.0.0.1:5173`). 시나리오가 예시 데이터를 넣고 건수를 확인하므로 데이터가 있는 DB에서는 실패합니다.

## 설정 (`.env`)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `haedap` / `haedap` / `haedap-local` | compose DB. 비밀번호는 DB 볼륨을 처음 만들 때만 적용됨 |
| `PORT` | `5173` | 화면 포트 |
| `HAEDAP_BIND` | `127.0.0.1` | `0.0.0.0`이면 LAN·서버 접속 허용 |
| `HAEDAP_ALLOWED_HOSTS` | (비움) | localhost 외에 접속에 쓰는 호스트 이름·IP(쉼표 구분) |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | (비움) | 둘 다 넣으면 근거 기반 모델 답변 선택 가능. backend에만 전달 |
| `MARITIME_DATA_API_TOKEN`, `MARITIME_DATA_DATABASE_URL` | (비움) | 해사 데이터 Tool 라우트(`/api/maritime-data/*`) 활성화 |
| `DATABASE_URL`, `HAEDAP_API_ORIGIN` | — | Docker 없이 실행할 때만 사용 (`DATABASE_URL`은 dbmate도 사용) |

## API

화면이 호출하는 경로는 모두 `/api/*`이며 Next.js가 backend로 전달합니다. 계약은 [`docs/specs/api.md`](docs/specs/api.md)에 있습니다.

| 경로 | 주요 기능 |
|---|---|
| `GET /api/health` | DB 연결·기능 상태와 CSRF 토큰 확인 |
| `POST /api/ask`, `/api/search` | 통합 답변과 문서 근거 검색 |
| `GET /api/documents`, `/api/operations`, `/api/reports` | 문서·운항·보고서 조회 |
| `POST /api/changes`, `/api/changes/review` | 관리자 변경과 승인 |
| `POST /api/tools/calculate_emissions`, `/api/tools/voyage_time` | 배출량·항해 시간 계산 |
| `/api/auth/*`, `/api/users`, `/api/history`, `/api/logs`, `/api/backups*` | 인증·사용자·이력·백업 |

변경 요청(POST)은 `GET /api/health` 응답의 `csrfToken`을 `X-Haedap-Token` 헤더에 넣어야 합니다.

## 해사 데이터 Tool (선택)

역할4의 실제 MRV·합성 Noon 전처리, PostgreSQL 적재, 읽기 전용 조회·CO₂ 계산 도구는 [`backend/maritime_data/`](backend/maritime_data/)에 있습니다. backend와 같은 uv 프로젝트이며, `MARITIME_DATA_API_TOKEN`을 설정하면 backend에 `/api/maritime-data/*`로 마운트됩니다(서버 전용 Bearer 토큰 필요, 화면 연결은 아직 없음). 설치·데이터 확보는 [사용 안내 10절](docs/user-guide.md#10-해사-데이터-tool-선택), 호출 형식은 [API 명세](docs/specs/maritime-data-api.md)를 보세요.

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/README.md`](docs/README.md) | 전체 문서 목록과 작성 규칙 |
| [`docs/architecture.md`](docs/architecture.md) | 시스템 구조, 요청 흐름, 모듈 경계, 보안 경계 |
| [`docs/data-model.md`](docs/data-model.md) | PostgreSQL 테이블, 상태 값, 백업 형식 |
| [`docs/user-guide.md`](docs/user-guide.md) | 사용·운영 안내, 버전 교체, 문제 해결 |
| [`docs/specs/`](docs/specs/) | API·통합 답변 등 구현 계약 |
| [`docs/plans/`](docs/plans/) | 작업 계획과 진행 상태 |
| [`docs/adr/`](docs/adr/) | 주요 설계 결정 기록 |
| [`docs/changelog.md`](docs/changelog.md) | 변경 이력과 검증 기록 |
| [`AGENTS.md`](AGENTS.md) | 사람·코딩 에이전트 공통 작업 지침 |

팀 보고서는 Notion에서 관리합니다: <https://app.notion.com/p/dudco/3dc1cb63e55380e98972eda1ce1040b5>
