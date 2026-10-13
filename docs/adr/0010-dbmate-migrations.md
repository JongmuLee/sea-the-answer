# 0010. DB 스키마는 최상위 `migrations/`의 SQL 파일로 관리하고 dbmate로 적용한다

- 상태: 채택
- 날짜: 2026-10-13
- 결정자: 윤영채 (프로젝트 구조·협업 담당)
- 관련: [ADR 0009](0009-split-packages-and-compose.md)(DB 초기화 방식 일부 대체), [`../data-model.md`](../data-model.md) 8절

## 배경

지금까지 앱 스키마는 `backend/pyapi/schema.sql` 하나였고, compose의 `db` 컨테이너가 **빈 볼륨으로 처음 시작할 때만** 이 파일을 실행했습니다. 그래서 이미 만들어진 DB에는 스키마 변경을 반영할 방법이 없었고, 바꾸려면 볼륨을 지우는 수밖에 없었습니다. 운영 데이터가 생기기 전에 변경 이력을 남기고 순서대로 적용하는 방법이 필요했습니다.

## 결정

- 앱 스키마의 원본은 저장소 최상위 `migrations/`의 SQL 파일입니다. 파일 이름은 `YYYYMMDDHHMMSS_설명.sql`이고, 한 파일 안에 `-- migrate:up`과 `-- migrate:down`을 둡니다. 첫 파일 `20261013000000_init.sql`은 이전 `schema.sql`과 같은 내용입니다.
- 적용 도구는 [dbmate](https://github.com/amacneil/dbmate)(`amacneil/dbmate:2.36.0` 이미지, 버전 고정)입니다. 적용 이력은 DB의 `schema_migrations` 테이블에 남고, 파일 하나가 트랜잭션 하나로 적용됩니다(실패하면 그 파일 전체가 롤백).
- compose의 일회성 `migrate` 서비스가 `docker compose up` 때마다 대기 중인 마이그레이션을 적용하고 끝납니다. backend는 이 서비스가 **성공으로 끝나야** 시작합니다. e2e 스택도 같은 파일로 DB를 만듭니다(`e2e-migrate`).
- backend 코드는 스키마를 만들거나 바꾸지 않습니다. 테이블이 없으면 `SCHEMA_NOT_READY`(503)를 반환합니다.
- 범위는 앱 스키마입니다. 역할4의 `maritime_data` 스키마는 CSV 적재와 묶여 있고 별도 DB일 수 있어서, 역할4와 합의한 뒤 옮깁니다.

## 검토한 대안

| 대안 | 장점 | 단점 / 채택하지 않은 이유 |
|---|---|---|
| Alembic | Python 생태계 표준 | ORM 모델이 없어 자동 생성 이점이 없음. 최상위 폴더에서 돌려도 backend Python 환경에 묶임 |
| Atlas | 선언형 diff, lint 등 기능이 가장 많음 | 도구가 무겁고, 배포판별 라이선스·기능 구분 확인이 필요함 |
| Flyway | 오래 검증됨 | Java 기반 이미지가 크고, 되돌리기(undo)는 유료 에디션 기능 |
| golang-migrate | 단일 바이너리 | up/down 파일이 따로이고, 실패 시 dirty 상태를 손으로 풀어야 함 |
| 기존 방식(initdb + `schema.sql`) | 추가 도구 없음 | 기존 DB에 변경을 반영할 수 없음 |

## 결과

- 좋아지는 점: 스키마 변경이 파일 단위 이력으로 남고, 기존 DB에도 `docker compose up`만으로 반영됨. SQL을 그대로 쓰므로 새로 배울 것이 적음. Docker 없이도 `npx dbmate`로 같은 파일을 적용할 수 있음.
- 감수하는 점: 마이그레이션 파일을 손으로 작성해야 함(자동 생성 없음). 도구가 하나 늘어남.
- 이 결정 때문에 지켜야 할 규칙:
  - **이미 커밋된(공유된) 마이그레이션 파일은 고치지 않습니다.** 변경은 항상 새 파일로 추가합니다.
  - `migrate:down`도 함께 작성합니다(되돌릴 수 없는 변경이면 그 이유를 주석으로 남김).
  - 스키마를 바꾸면 `docs/data-model.md`를 같은 작업에서 고치고, e2e를 실행합니다.
