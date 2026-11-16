# 보안 신호 1차 조사 입력 계약

실행 파일: [`tools/run_security_agent.py`](../../tools/run_security_agent.py). 이 문서는 수강생이 AI가 **무엇을 보고 무엇을 못 보는지** 이해하기 위한 설명이다. 실제 명령은 `make agent-security`다.

## 입력

- `measurements/security-normal-*.json`: 가짜 회원의 단발 오타와 정상 재로그인
- `measurements/security-spray-*.json`: 존재하지 않는 가짜 계정 여러 개의 실패, 한 가짜 계정의 반복 실패와 HTTP 429
- `measurements/security-idor-*.json`: 가짜 Bob의 본인 주문과 여러 타인 주문 조회 결과
- 각 파일의 요청 이름, HTTP 상태, 본문 `code`, `trace_id`, 대상 주문 번호, Loki 이벤트 이름별 **건수**만 보낸다.

비밀번호, 로그인 토큰, 원본 요청 본문, 실제 회원 정보, 원본 로그 줄은 보내지 않는다. 특히 **반품 신청의 레드 결과와 구현 코드는 제공하지 않는다.** 조회 거절을 본 AI가 반품 결함을 이미 안다고 쓰지 못하게 하기 위해서다.

## 기대 출력

`observations`는 자료에서 읽은 사실, `hypotheses`는 정상 반례를 포함한 조사 가설, `unknowns`는 아직 시험하지 않은 내용을 적는다. `evidence_refs`는 위 파일 이름과 `step` 또는 `trace_id`를 붙인다. `next_action`은 사람이 가짜 계정으로 반품 API 소유권 확인 시험을 하자는 제안이다. **침해 성공·취약점 확인·차단 완료**로 쓰면 실패다.

## 사람의 다음 단계

사람이 [레드·그린 확인 시험](../evidence/security-return-triage-20261114.md)을 검토한다. 온콜 웹의 `확인 시험으로 결함 확인`에는 실제 저장된 시험 파일을 적고, 그다음 별도의 `수정 요청 승인`을 누른다. 파일 경로 칸은 사람이 검토한 근거를 **기록**하는 기능이다. 서버가 파일 내용을 자동 검증한다는 뜻은 아니다. 승인 전 AI에게 코드 쓰기 권한을 주지 않는다.
