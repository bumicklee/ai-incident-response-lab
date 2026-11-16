# AI 에이전트로 하는 백엔드 장애·해킹 징후 탐지와 수정 · 실습 랩

인프런 강의 「AI 에이전트로 하는 백엔드 장애·해킹 징후 탐지와 수정」의 실습 저장소입니다.
가짜 쇼핑몰, 로그 수집(Alloy·Loki), 경보(Grafana), 온콜 웹, AI 분석·수정 하네스를 내 PC의 Docker 안에서 함께 띄웁니다.

> 모든 주소는 `127.0.0.1` 전용이고, 회원·주문·비밀번호는 모두 교육용 가짜 값입니다. 실제 서비스나 다른 사람의 시스템에 쓰지 마세요.

## 준비물

- macOS (강의는 macOS에서 확인했습니다), 메모리 16GB 권장
- Docker Desktop, Python 3, make, git
- AI 분석·수정 실습: 클로드 코드를 쓸 수 있는 Claude 유료 구독 로그인(또는 분석용 Anthropic API 키)

## 받기와 첫 실행

```bash
git clone https://github.com/bumicklee/ai-incident-response-lab.git lab
cd lab
make start
```

`make start`는 점검 → 쇼핑몰 이미지 빌드 → 컨테이너 기동 → 스모크 시험을 차례로 합니다.
처음에는 이미지를 만드느라 몇 분 걸리고, 마지막에 「기동과 로그 수집 확인」 줄이 나오면 준비된 것입니다.

강의와 같은 폴더 이름(`lab`)으로 받도록 `git clone` 끝에 `lab`을 붙였습니다.
ZIP으로 받아도 됩니다. GitHub 화면의 **Code → Download ZIP**으로 받은 뒤 압축을 풀고, 풀린 폴더 이름을 `lab`으로 바꾼 다음 그 안에서 `make start`를 실행합니다.
강의에서 「실습 폴더」라고 하면 이 `lab` 폴더입니다.

## 주소

| 서비스 | 주소 |
| --- | --- |
| 쇼핑몰 API | http://127.0.0.1:18085 |
| Grafana (사용자 `admin`, 비밀번호는 `compose.yaml`의 `GF_SECURITY_ADMIN_PASSWORD`) | http://127.0.0.1:13030 |
| 온콜 웹 (`make setup-oncall` → `make up-oncall` 뒤) | http://127.0.0.1:18090 |
| ntfy 알림 (선택) | http://127.0.0.1:18091 |

## 자주 쓰는 명령

```bash
grep -E '^[a-z-]+:.*##' Makefile   # 명령 목록과 설명
make ps            # 컨테이너 상태
make logs          # 최근 로그
make down          # 끄기(데이터는 유지)
```

수업마다 쓰는 명령(`make traffic`, `make up-alerts`, `make analysis-cli`, `make ai-fix` 등)은 강의와 슬라이드 교재에 순서대로 나옵니다.

## 폴더

| 폴더 | 내용 |
| --- | --- |
| `spring/` | 실습용 쇼핑몰(Spring Boot)과 보안 부품 `secure-kit-spring` |
| `infra/` | 게이트웨이, OpenBao, Alloy·Loki·Grafana 설정과 경보 규칙 |
| `oncall/` | 온콜 웹(사건함·승인·AI 보고서) |
| `agent/` | 클로드 코드 수정 하네스의 작업 규칙(`CLAUDE.md`) |
| `tools/` | 트래픽·공격 재현, 분석·수정 하네스, 검증 도구 |
| `course/` | 수업에서 읽는 이벤트 목록, 분석 프롬프트, 수정 과제용 패치 |
| `measurements/` | 내 실행 결과가 쌓이는 곳(처음에는 `make agent-rca`용 고정 자료만 있음) |

## 막히면

| 증상 | 해결 |
| --- | --- |
| Cannot connect to the Docker daemon | Docker Desktop을 켜고 고래 아이콘이 멈출 때까지 기다립니다 |
| 포트 사용 중 | 같은 포트를 쓰는 프로그램을 끄거나 `make down`으로 다른 실습장을 끕니다 |
| 3분 안에 준비되지 않았습니다 | `make ps`·`make logs`로 확인하고, Docker 디스크가 가득 찼으면 오래된 빌드 캐시를 지웁니다 |

## 라이선스

- `spring/`의 쇼핑몰 원본은 `spring/LICENSE`를 따릅니다.
- `spring/secure-kit-spring/`은 MIT 라이선스입니다.
