# 0014. 동국 AI Chat API 게이트웨이(Mindlogic) 호스트 계약 추가

- 상태: 제안
- 날짜: 2026-10-14
- 결정자: 윤영채 (팀원 검토 필요)
- 관련: [ADR 0013](0013-provider-credential-isolation.md), [공급자 계약](../specs/llm-provider.md)

## 배경

ADR 0013은 `dongguk.edu`와 그 하위 도메인에만 `DONGUK_AI_CHAT_API_KEY`를 보내고, "동국의 별도 외부 도메인은 공식 접속 정보를 확인한 뒤 별도 호스트 계약을 추가한다"고 남겼다. 동국 AI Chat(Mindlogic FactChat)의 API 기본 주소는 `https://factchat-cloud.mindlogic.ai/v1/gateway`로 확인되었다(서비스의 API Gateway 안내, 2026-10-14 팀원 확인). 이 호스트는 `dongguk.edu`가 아니므로 기존 규칙으로는 키 없이 호출되어 인증에 실패한다.

Mindlogic 공개 문서는 Chat Completions 경로를 끝 슬래시가 있는 `/v1/gateway/chat/completions/`로 표기한다. 앱은 리다이렉트를 따라가지 않으므로(`follow_redirects=False`) 슬래시 없는 경로가 리다이렉트되면 실패한다.

## 결정

- 파싱한 호스트가 **정확히** `factchat-cloud.mindlogic.ai`(대소문자·끝 점 무시)일 때 `DONGUK_AI_CHAT_API_KEY`를 선택한다. `*.mindlogic.ai` 와일드카드는 쓰지 않는다. Mindlogic은 여러 기관이 함께 쓰므로 다른 하위 도메인으로 동국 키를 보내지 않는다.
- 이 호스트에서는 요청 경로를 문서 표기대로 `/chat/completions/`(끝 슬래시 포함)로 붙인다. 다른 호스트는 기존 `/chat/completions`를 유지한다.
- 동국 키가 없으면 OpenAI 키로 대체하지 않고 비활성으로 판정한다(ADR 0013 원칙 유지).

## 검토한 대안

| 대안 | 장점 | 채택하지 않은 이유 |
|---|---|---|
| `*.mindlogic.ai` 전체 허용 | 다른 게이트웨이 주소에도 대응 | 다른 기관·서비스 하위 도메인으로 키가 전송될 수 있음 |
| 같은 호스트 리다이렉트만 따라가기 | 경로 표기 차이에 일반적으로 대응 | 리다이렉트 처리 중 인증 헤더 취급을 따로 검증해야 함. 문서 경로를 직접 쓰는 편이 단순 |
| 키를 보낼 호스트를 환경변수로 지정 | 주소 변경에 코드 수정 불필요 | 설정 실수로 임의 호스트에 키가 전송될 수 있어 ADR 0013의 격리 의도와 충돌 |

## 결과

- `LLM_API_BASE_URL=https://factchat-cloud.mindlogic.ai/v1/gateway`와 동국 키·모델 ID로 동국 AI Chat을 호출할 수 있다.
- 모의 HTTP 테스트로 키 선택·경로·유사 도메인 차단을 확인했다. 실제 게이트웨이 호출(슬래시 경로 수용 여부 포함)은 아직 미검증이다.
- 게이트웨이 호스트가 바뀌면 이 계약을 새 ADR로 갱신한다.
