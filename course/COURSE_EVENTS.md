# 이 강의에서 실제로 읽는 이벤트

기존 [`SECURITY_EVENTS.md`](SECURITY_EVENTS.md)는 원본 쇼핑몰의 전체 보안 이벤트 목록이다. 이 강의에서는 아래 이름만 먼저 쓴다. 앱이 HTTP 상태와 별도로 `SECURITY` JSON 로그를 남기고, Alloy가 Loki의 `stream=security`로 모은다.

| 이벤트 또는 로그 | 이 랩에서 뜻하는 것 | 판단할 때 더 필요한 것 |
| --- | --- | --- |
| `error.unhandled` | 앱이 처리하지 못한 예외를 관측했다 | 같은 `trace_id`의 요청 결과·입력·스택·코드 |
| `auth.denied.token` | JWT 없음·만료·검증 실패 등 인증 거부 | 토큰 갱신·정상 미로그인·실패한 계정 수 |
| `auth.login.failed` | 아이디 또는 비밀번호가 맞지 않았다. 계정명은 기록하지 않음 | IP·기기·시간·성공 전환·공유 IP 반례 |
| `auth.login.failed_burst` | 10분 창에서 한 IP/기기의 서로 다른 계정 실패가 10개에 도달했다 | 프록시·공유 IP 여부와 정상 업무 반례 |
| `auth.login.rate_limited` | 요청 중 로그인 속도 제한이 동작했다 | HTTP 429와 재시도 안내, 정상 계정 영향 |
| `auth.login.succeeded` | 가짜 회원 로그인에 성공했다 | 앞선 실패와 같은 주체인지. 성공이 공격 부재 증거는 아님 |
| `access.denied.not_owner` | 다른 회원 주문 접근을 서버가 거부했다 | 서로 다른 주문 수, 원문 `trace_id`, 다른 API의 확인 시험 |

**세 숫자를 섞지 않는다.** HTTP `401/404/429/500`, JSON 본문의 `code`, 보안 이벤트 이름은 서로 다른 기록이다. 타인 주문 조회는 이 앱에서 `HTTP 200 / body code 500`으로 거부될 수 있다. 조회 거절이 곧 반품 신청의 보호를 뜻하지 않는다.

`auth.login.failed_burst`는 기존 Redis 제한기가 만든다. 이것은 **조사 신호**이며 크리덴셜 스터핑 성공의 증거가 아니다. `auth.login.failed`와 `auth.login.succeeded`는 이 강의를 위해 더한 구조화 감사 이벤트다. 비밀번호, 토큰, 전화번호, 원본 요청 본문은 로그에 넣지 않는다. IP와 User-Agent도 실제 서비스로 옮기면 보존 기간과 접근 권한을 별도로 정해야 한다.
