# 해사 문서 전처리와 청킹

- 상태: 완료 (최신 main 통합·검증 완료, 머지 커밋 대기)
- 작성: Codex · 2026-10-13
- 담당: 문서/RAG
- 관련: [API 계약](../specs/api.md), [아키텍처](../architecture.md)

## 1. 배경과 목표

현재 실행 경로는 PDF.js → `document.save` → Python `api/store/documents.py`의 `DocumentStore.save_document` → PostgreSQL이다. 기존 900자 고정 분할을 페이지·조항·문장 경계를 고려한 순수 Python 함수로 교체한다. 최신 main의 독립 패키지·Docker·마이그레이션 구조를 유지한다.

## 2. 범위

- 포함: `data/pdfs/ABS/`의 Part 5D(387쪽), Part 6(506쪽) 각 첫 45쪽 분석, 독립 전처리 모듈, 최소 저장 연결, 단위·실제 PostgreSQL 검증.
- 제외: 임베딩·벡터 검색, OCR·표 셀/수식 복원, 업로드 한도 변경, 기존 문서 재색인. 초기 구현 후 별도 요청으로 최신 main을 통합하며 커밋·푸시는 하지 않는다.

## 3. 단계

- [x] 현재 코드·테스트 및 PDF.js 실제 추출 결과 확인
- [x] 대표 페이지 렌더 확인: Part 5D 물리 20·32쪽, Part 6 물리 23·31쪽
- [x] 전처리 모듈과 최소 DB 연결 구현
- [x] 가상 자료 단위 테스트 및 실제 PDF → PostgreSQL → 검색 검증
- [x] `npm test`, 변경 검토, 실행·머지 안내 기록 (`npm test`의 기존 Windows 실행기 테스트 실패는 아래 기록)
- [x] 최신 main `196ad73` 통합 및 새 API 경로 연결 (머지 커밋 전 정지)
- [x] 새 backend 전체 pytest·실 DB RAG 회귀·PDF 검증
- [x] 새 frontend 테스트·빌드·독립 테스트 스택의 브라우저 검증 (Docker 미설치로 로컬 격리 스택 사용)

완료 조건: 페이지·본문 보존, 제목/조항 경계 분할, 중복·개정·권한 회귀 확인. 실제 자료는 무시되는 로컬 경로에만 두고 테스트 로그에는 본문을 출력하지 않는다.

## 4. 영향 받는 파일·문서

| 경로 | 변경 |
|---|---|
| `backend/api/rag_preprocessing.py` | 저장소·API에 의존하지 않는 전처리 함수. 운영 Docker 이미지의 `api` 복사에 포함 |
| `backend/api/store/documents.py` | import와 청킹 루프 연결 |
| `backend/tests/test_rag_preprocessing.py` | 가상 자료·선택적 PostgreSQL 테스트 |
| `backend/tests/test_rag_postgres.py`, `rag_postgres_support.py` | 임시 스키마 API/DB 회귀 |
| `e2e/verify-rag-pdf.mjs`, `backend/tests/verify_rag_pdf.py` | 실제 UI 추출 함수 → 선택 페이지 API/DB 검증 |
| `docs/specs/api.md`, `docs/architecture.md`, `docs/changelog.md` | 계약·연결·검증 |

## 5. 위험과 대응

| 위험 | 대응 |
|---|---|
| 표/수식 추출 손실 | 숫자·기호를 추측하지 않음. 행은 보존하되 표 의미 복원은 별도 작업 |
| 반복 본문 삭제 | 3개 이상 서로 다른 페이지 가장자리에서 반복되는 명시적 running header/footer만 제거 |
| 기존 인용 ID 변경 | 기존 hash/개정 규칙 유지. 동일 입력은 unchanged, 기존 청크 재작성 없음 |
| 폴더 변경 충돌 | 독립 모듈을 `api` 패키지에 두고 `api/store/documents.py`에 import와 분할 루프만 이식 |
| 사용자 DB 오염 | 검증은 임시 스키마·트랜잭션 롤백 사용 |

## 6. 열린 질문

전체 문서의 수식·병합 셀·2단 본문 복원은 좌표 기반 추출 또는 OCR을 포함하는 별도 범위이다. 이번 표본에는 일반 2단 본문과 완전한 빈 페이지가 없어 가상 자료와 별도로 구분한다.

## 7. 진행 기록

- 2026-10-13: Part 5D의 조항, Part 6의 표/각주, 반복 머리말·꼬리말 확인. 7개 원본 중 5개가 기존 25MB 제한을 초과하므로 한도 안의 2개를 선정. PDF.js 추출 단계에서 일부 수식 문자가 누락되고 병합 셀 순서가 평탄화됨. 프런트엔드 수정으로 완전 복원을 시도하지 않고 이 한계를 명시한다.
- 2026-10-13: 실제 UI `extractPdf`로 Part 5D 387쪽, Part 6 506쪽 전체의 텍스트 추출 성공(각 빈 텍스트 페이지 0). 대표 페이지의 청킹·API/DB 저장·검색·PDF 원본 반환을 검증하고 임시 스키마를 롤백했다. Part 5D 물리 20/21/22쪽 → 21청크, Part 6 물리 23/30/31쪽 → 13청크. 기존 고정 분할은 해당 표본에서 단어 중간을 각각 3회/2회 잘랐다. 검색 정확도 전체가 개선됐다는 평가 결과는 아니다.
- 2026-10-13: 최신 main 통합 후 격리된 PostgreSQL 17에서 backend 151 passed / 4 skipped(별도 해사 원본 데이터셋 미설정), frontend 4 passed·production 빌드, 두 PDF의 같은 페이지·청크 결과, production standalone 브라우저 전체 시나리오 통과. 테스트 서버는 종료했다. 상세 환경·제약은 [검증 기록](../changelog.md#2026-10-13--rag-최신-main-통합-검증)을 참고한다.

## 8. 실행과 재현

앱 실행은 [최상위 README](../../README.md#실행하기-docker)를 따른다. 기존 `.env`를 덮어쓰지 않는다. Docker의 DB 볼륨은 기존 로컬 PostgreSQL과 별개이며, 기존 데이터 이전은 이 통합 범위에 포함하지 않는다. 전처리용 추가 런타임 패키지는 없다.

```powershell
# 저장소 최상위: Docker로 앱 실행
docker compose up --build -d
```

관리자 로그인 → 문서 등록 → PDF 선택 → 추출 본문 검토 → 저장 → 검색 후 페이지 확인. 기존 25MB/1,000쪽/총 200만자 한도가 유지된다. 이 검증은 선택 페이지에 대한 API 테스트이며 사용자 데이터에 문서를 영구 등록하지 않았다. 같은 문서를 이미 저장했다면 unchanged이므로 새 청킹이 자동 적용되지 않는다.

```powershell
# backend 전체 테스트 (저장소 최상위에서)
uv run --locked --directory backend pytest
# frontend 단위 테스트
npm.cmd --prefix frontend test
# 브라우저 검증 도구 의존성 설치
npm.cmd --prefix e2e ci

# 실제 PDF 검증: Windows는 설치된 Edge, 그 외는 Playwright Chromium 사용
node e2e/verify-rag-pdf.mjs "data/pdfs/ABS/MVR Part-5D-Offshore-Support-Vessels-for-Specialized-Services-jul-2026.pdf" 20,21,22
node e2e/verify-rag-pdf.mjs "data/pdfs/ABS/MVR Part-6-Specialized-Items-and-Systems-jul-2026.pdf" 23,30,31
```

실제 PDF 검증 도구는 `.env`의 `DATABASE_URL` 또는 `HAEDAP_TEST_DATABASE_URL`을 사용한다. 연결 사용자는 임시 스키마를 만들 권한이 필요하다. 임의 이름의 스키마를 만든 뒤 모든 변경을 롤백하며 실제 테이블을 수정하지 않는다. PDF/본문은 메모리에서만 전달하고 stdout에는 페이지·개수·성공 여부만 출력한다. 브라우저 경로를 지정하려면 `CHROME_PATH`를 사용한다. 한 번에 한 파일, 최대 10개 물리 페이지를 선택한다.

가상 자료의 실 DB 회귀는 `HAEDAP_TEST_DATABASE_URL`을 설정한 뒤 `backend/`에서 `uv run --locked pytest tests/test_rag_postgres.py -q`로 실행한다. 미설정 시 2개 테스트는 skip한다. 일반 pytest 명령은 `.env`를 자동으로 읽지 않는다. 지원 코드는 임시 스키마 안에서 앱 init migration의 `up` 부분만 적용한다. 팀의 전체 DB 회귀 테스트까지 켜려면 임시 DB를 생성하는 권한이 필요하므로 별도의 테스트 PostgreSQL을 권장한다. 기존 앱 계정의 권한을 올릴 필요는 없다.

## 9. split-packages 통합

`upstream/main`의 `196ad73`을 `feature/rag`에 `--no-commit --no-ff`로 병합했다. 삭제/수정 충돌은 옛 `backend/pyapi/workspace_store.py`와 `scripts/run-python-tests.mjs`에서, 내용 충돌은 `docs/changelog.md`와 `docs/specs/api.md`에서 발생했다. 팀의 새 구조와 문서를 유지하고 RAG 계약·검증 기록을 합친다.

전처리 본문은 그대로 `backend/api/rag_preprocessing.py`로 이동한다. `api/`에 두므로 운영 Dockerfile을 바꾸지 않고 포함된다. 저장 코드는 상대 import로 연결하고 해시·기존 개정본 분기는 유지한다. 테스트의 import·API monkeypatch 대상은 `api.main`, `api.deps`, `api.web.security`로 맞춘다. PDF 검증 도구는 기존 e2e Playwright 의존성을 사용하며 Python 보조 도구는 backend 테스트 폴더로 이동한다. 삭제된 루트 npm 실행기를 복원하지 않고 backend pytest의 자동 발견을 사용한다.
