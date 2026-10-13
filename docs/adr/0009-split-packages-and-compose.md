# 0009. frontend·backend를 독립 패키지로 나누고 최상위 docker compose로 함께 실행한다

- 상태: 채택 — DB 초기화(`db`의 initdb로 `schema.sql` 적용)는 [0010](0010-dbmate-migrations.md)으로 대체
- 날짜: 2026-10-13
- 결정자: 윤영채 (프로젝트 구조·협업 담당)
- 관련: [ADR 0003](0003-sqlite-fts5-local-storage.md)(대체), [ADR 0005](0005-dev-mode-direct-processes.md)(일부 대체), [ADR 0008](0008-tools-query-and-calculation-boundary.md)(실행 위치 대체), [`../architecture.md`](../architecture.md)

## 배경

API가 FastAPI·PostgreSQL로 옮겨진 뒤에도 저장소는 Node 시절 구조였습니다. 최상위 `package.json`이 Next.js 화면, 실행기(`scripts/run.mjs`가 uvicorn까지 띄움), 쓰이지 않는 Node/SQLite 백엔드 테스트, Playwright를 모두 갖고 있었고, `backend/maritime_data`는 자기 `pyproject.toml`과 별도 FastAPI 앱을 따로 가지고 있었습니다. 운영 환경은 Ubuntu 서버의 Docker로 정했으므로 서비스별 이미지와 한 번에 띄우는 방법이 필요했고, 화면부터 DB까지 실제로 거치는 회귀 테스트를 최상위에서 돌릴 수 있어야 했습니다.

## 결정

- `frontend/`는 자체 `package.json`·`package-lock.json`(npm)을, `backend/`는 자체 `pyproject.toml`·`uv.lock`(uv)을 가진 독립 프로젝트로 둡니다. 각 폴더 안에서 설치·실행·테스트가 끝나야 하며, 서로의 파일을 import하지 않습니다(계약은 HTTP `/api/*`).
- 최상위에는 패키지 매니저를 두지 않습니다. 최상위 `compose.yaml`이 `db`(PostgreSQL)·`backend`·`frontend`를 띄우고, `compose.dev.yaml` 오버레이가 소스 마운트·핫 리로드를 더합니다. 각 서비스의 `Dockerfile`은 해당 폴더에 둡니다(`prod`·`dev` 타깃).
- 화면→API→DB 전체 흐름 테스트는 최상위 `e2e/`(자체 `package.json`, Playwright Test)에 둡니다. `docker compose --profile e2e up --build --exit-code-from e2e e2e`가 tmpfs DB를 쓰는 별도 스택(`e2e-db`·`e2e-backend`·`e2e-frontend`)과 테스트 러너를 띄웁니다. 앱 데이터와 섞이지 않고, 매 실행이 빈 DB에서 시작합니다.
- 쓰이지 않던 Node/SQLite 백엔드(`backend/*.mjs`)와 `scripts/`(Node 실행기·Windows 실행기·SQLite 점검·JSON 수집)를 제거합니다. PostgreSQL DB를 실제로 만든 적이 없고 SQLite에도 옮길 데이터가 없어서 SQLite → PostgreSQL 이전 도구(`pyapi/migrate_sqlite.py`)도 제거합니다.
- `backend/maritime_data`는 backend uv 프로젝트로 합치고, `MARITIME_DATA_API_TOKEN`이 있을 때 같은 FastAPI 앱에 `/api/maritime-data/*` 라우터로 마운트합니다. 데이터 준비용 무거운 의존성(pandas, openpyxl)은 `data` 의존성 그룹으로 분리합니다.
- 최상위 폴더는 `frontend/`, `backend/`, `e2e/`, `docs/`입니다.

## 검토한 대안

| 대안 | 장점 | 단점 / 채택하지 않은 이유 |
|---|---|---|
| 최상위 `package.json` 유지(npm workspaces) | 명령 하나로 설치 | Python 쪽과 대칭이 안 맞고, 서비스별 이미지가 루트 lockfile에 묶임 |
| 해사 데이터 Tool을 별도 컨테이너로 | 독립 배포·장애 격리 | 서비스·토큰·포트가 하나 더 늘어남. 같은 DB·같은 언어라 지금 규모에선 이득이 작음 |
| e2e를 frontend 패키지 안에 | 폴더 하나 감소 | e2e는 frontend만이 아니라 전체 스택을 검증. 브라우저 의존성이 frontend 이미지·설치에 섞임 |
| Makefile·셸 스크립트로 묶기 | 짧은 명령 | Windows 팀원 환경과 맞지 않음. `docker compose` 명령은 모든 OS에서 동일 |

## 결과

- 좋아지는 점: 서비스별 의존성·이미지가 분리되어 배포가 단순해짐. `docker compose up` 하나로 DB까지 준비됨(스키마 자동 적용). e2e가 실제 PostgreSQL·FastAPI·Next standalone을 거침.
- 감수하는 점: Docker 없이 실행하려면 PostgreSQL을 직접 준비하고 두 폴더에서 각각 명령을 실행해야 함. Windows 전용 실행기(`start.cmd`)는 사라짐. 이전 Node 구현의 FTS5·BM25 검색은 git 기록에서만 볼 수 있음.
- 이 결정 때문에 지켜야 할 규칙:
  - 새 의존성은 해당 폴더의 매니저로만 추가합니다(`frontend/`에서 `npm install`, `backend/`에서 `uv add`).
  - 공개 포트는 frontend 하나입니다. backend·db는 compose 내부 네트워크에 둡니다.
  - 화면 흐름에 영향이 있는 변경은 e2e를 실행합니다.
