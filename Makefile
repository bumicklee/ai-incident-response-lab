PYTHON ?= python3
COMPOSE = docker compose -f compose.yaml

.PHONY: doctor config build up down ps logs plan smoke snapshot baseline baseline-all baseline-summary security-baseline alert-threshold-check setup-oncall up-oncall up-alerts stop-oncall replay-incidents up-gateway stop-gateway gateway-probe traffic security-normal security-spray security-idor security-shared-links agent-rca agent-security analysis-demo-rca analysis-demo-security analysis-cli analysis-api analysis-watch-cli analysis-watch-api approval-gate propose-fix fix-candidate-unit fix-candidate-build fix-candidate-up fix-candidate-verify fix-candidate-down up-kit down-kit kit-login kit-stats start ai-fix ai-fix-diff ai-fix-test ai-fix-verify ai-pr ai-merge ai-fix-deploy ai-fix-check ai-fix-rollback

start: ## 처음 한 번에 켜기: 점검 → 이미지 빌드 → 기동 → 스모크(준비될 때까지 15초 간격으로 최대 12번 다시 확인)
	@if [ -z "$$($(COMPOSE) ps -q mall-portal 2>/dev/null)" ]; then $(PYTHON) tools/doctor.py; else echo "실습장이 이미 켜져 있어 포트 점검(make doctor)은 건너뜁니다"; fi
	@$(COMPOSE) build mall-portal
	@$(COMPOSE) up -d
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12; do \
		if $(PYTHON) tools/smoke.py; then exit 0; fi; \
		echo "아직 준비 중입니다. 15초 뒤 다시 확인합니다($$i/12)"; sleep 15; \
	done; echo "3분 안에 준비되지 않았습니다. make ps와 make logs로 확인하세요"; exit 1

doctor: ## Mac·Docker·포트·파일 준비 확인. 컨테이너 상태는 바꾸지 않음
	@$(PYTHON) tools/doctor.py

config: ## Compose 구조 확인
	@$(COMPOSE) config --quiet

build: ## Spring 이미지 빌드
	@$(COMPOSE) build mall-portal

up-kit: ## secure-kit 로그인 단원: 로그인에 킷의 속도 제한을 켠 고객 API로 바꾼다
	@$(COMPOSE) -f compose.kit.yaml up -d --no-deps --force-recreate mall-portal

down-kit: ## 킷 로그인 단원을 끝내고 기본(레드) 고객 API로 되돌린다
	@$(COMPOSE) up -d --no-deps --force-recreate mall-portal

kit-stats: ## 최근 15분 로그인 보안 이벤트 통계(이벤트·출처·사유·대상·분 단위)
	@python3 tools/login_event_stats.py --minutes 15

kit-login: ## 킷 로그인 단원 시나리오: 정상 오타 · 한 계정 대입 · 여러 계정 대입(크리덴셜 스터핑)
	@python3 tools/kit_login_scenario.py

up: ## 준비 스택 기동. 측정 시작 전에 doctor 실행
	@$(COMPOSE) up -d

down: ## 준비 스택 내리기. 볼륨은 유지
	@$(COMPOSE) down

ps: ## 컨테이너 상태
	@$(COMPOSE) ps

logs: ## 고객 API 로그
	@$(COMPOSE) logs --tail=100 mall-portal

plan: ## 목록의 측정 항목을 JSON으로 추출
	@$(PYTHON) tools/prepare_manifest.py

smoke: ## API·Grafana·Loki·Alloy 로그 수집 확인(읽기 전용)
	@$(PYTHON) tools/smoke.py

snapshot: ## 컨테이너 목록과 메모리 순간값 저장(최대값 아님)
	@$(PYTHON) tools/capture_services.py

RUN ?= 1
baseline: ## 평소 트래픽 30분 1회. RUN=1..5, 1~3은 같은 시드
	@$(PYTHON) tools/traffic_baseline.py --seconds 1800 --rps 2 --run $(RUN)

START ?= 1
baseline-all: ## START부터 5회차까지 순서대로, 각 회차 품질 검사 뒤 다음 실행
	@$(PYTHON) tools/collect_baselines.py --start $(START) --end 5

baseline-summary: ## 30분 5회가 모두 끝났을 때만 기준선 승인
	@$(PYTHON) tools/summarize_baselines.py

security-baseline: ## 완료한 30분 5회 구간의 Loki 보안 이벤트를 5분별로 다시 집계
	@$(PYTHON) tools/collect_security_baseline.py

alert-threshold-check: ## 완료한 정상 구간의 1분 로그 창이 경보 임계값을 넘는지 검사
	@$(PYTHON) tools/check_alert_thresholds.py

setup-oncall: ## 로컬 온콜 웹·ntfy 비밀값 만들기(.env·runtime은 Git 제외)
	@$(PYTHON) tools/setup_oncall.py

up-oncall: ## 기준선 실측 완료 뒤 온콜 웹·ntfy 추가 기동
	@docker compose -f compose.yaml -f compose.oncall.yaml up -d --build ntfy oncall

up-alerts: ## 기준선 뒤 Grafana에 500·보안 신호 경보 규칙 로드
	@docker compose -f compose.yaml -f compose.oncall.yaml up -d grafana

stop-oncall: ## 온콜 웹·ntfy 컨테이너만 중지·제거(사건 기록 볼륨은 유지). 기본 랩은 유지
	@docker compose -f compose.yaml -f compose.oncall.yaml rm -s -f oncall ntfy

replay-incidents: ## 과거 실측 두 사건을 온콜 웹에 재생(실시간 Grafana 경보 아님)
	@$(PYTHON) tools/replay_incidents.py

up-gateway: ## 기준선 뒤 두 번째 로그 출처(작은 nginx 게이트웨이) 기동
	@docker compose -f compose.yaml -f compose.gateway.yaml up -d gateway

stop-gateway: ## 게이트웨이 컨테이너만 중지·제거
	@docker compose -f compose.yaml -f compose.gateway.yaml rm -s -f gateway

gateway-probe: ## 가짜 Bob 요청 하나가 게이트웨이·앱 보안 로그에 같은 trace ID로 남는지 확인
	@$(PYTHON) tools/gateway_probe.py

agent-rca: ## 제한된 실측 packet으로 Claude 읽기 전용 500 RCA 생성
	@$(PYTHON) tools/run_rca_agent.py

agent-security: ## 정상 반례·로그인 남용·소유권 거부 자료로 읽기 전용 보안 조사
	@$(PYTHON) tools/run_security_agent.py

analysis-demo-rca: ## 실측 trace를 가진 합성 500 재생 사건 하나 만들기
	@$(PYTHON) tools/create_analysis_demo.py --kind rca

analysis-demo-security: ## 실측 trace를 가진 합성 보안 재생 사건 하나 만들기
	@$(PYTHON) tools/create_analysis_demo.py --kind security

analysis-cli: ## 로컬 Claude CLI로 대기 사건 하나 분석. INCIDENT=번호 선택 가능
	@$(PYTHON) tools/local_analysis_worker.py --backend cli $(if $(INCIDENT),--incident-id $(INCIDENT),)

analysis-api: ## 로컬 작업자에서 Claude API 키로 대기 사건 하나 분석
	@$(PYTHON) tools/local_analysis_worker.py --backend api $(if $(INCIDENT),--incident-id $(INCIDENT),)

analysis-watch-cli: ## 이 PC에서 CLI 작업자 한 개만 계속 실행
	@$(PYTHON) tools/local_analysis_worker.py --backend cli --watch

analysis-watch-api: ## 이 PC에서 API 작업자 한 개만 계속 실행
	@$(PYTHON) tools/local_analysis_worker.py --backend api --watch

approval-gate: ## INCIDENT=번호의 RCA·사람 수정 승인 기록이 없으면 수정 단계 차단
	@$(PYTHON) tools/approval_gate.py --incident-id $(INCIDENT)

propose-fix: ## INCIDENT=번호의 승인 후 AI가 한 파일의 검토용 패치 제안(적용·배포 없음)
	@$(PYTHON) tools/propose_fix_agent.py --incident-id $(INCIDENT)

fix-candidate-unit: ## 격리 사본에 패치를 적용해 Java 가격 회귀 시험 4건 실행
	@$(PYTHON) tools/build_fix_candidate.py --unit-only

fix-candidate-build: ## 본편 소스를 바꾸지 않고 격리 사본에서 수정 후보 이미지 빌드
	@$(PYTHON) tools/build_fix_candidate.py

fix-candidate-up: fix-candidate-build ## 수정 후보를 별도 포트 19186에서 기동. 기본 랩을 먼저 켜야 함
	@docker compose -f compose.yaml -f compose.fix-candidate.yaml up -d --no-deps mall-portal-candidate

fix-candidate-verify: ## 가격 누락·1원 변조·정상 주문의 HTTP 결과와 서버 가격 확인
	@LAB_PORTAL_PORT=19186 $(PYTHON) tools/verify_fix_candidate.py

fix-candidate-down: ## 수정 후보 컨테이너만 중지·제거. 기본 랩은 유지
	@docker compose -f compose.yaml -f compose.fix-candidate.yaml rm -s -f mall-portal-candidate

traffic: ## 레드 랩에 정상+500+접근 거부 주입. 기준선 5회 완료 뒤 실행
	@$(PYTHON) tools/incident_traffic.py

security-normal: ## 가짜 회원의 단발 로그인 오타·정상 재로그인
	@$(PYTHON) tools/security_scenarios.py --scenario normal

security-spray: ## 가짜 계정만으로 로그인 실패·429·failed_burst 확인
	@$(PYTHON) tools/security_scenarios.py --scenario spray

security-idor: ## 가짜 Bob의 본인 주문·여러 타인 주문 조회 비교
	@$(PYTHON) tools/security_scenarios.py --scenario idor

security-shared-links: ## 정상 역할 Bob의 서로 다른 가족 공유 링크 3개 거부·오탐 반례
	@$(PYTHON) tools/security_scenarios.py --scenario shared-links --interval-seconds 150

# ── AI 수정 하네스: 승인된 사건 → 제한된 Claude Code가 시험·최소 수정·PR.md → 사람이 리뷰·머지·배포 ──
# TASK=login|price|owner. Claude는 tmp/ai-fix/<과제>/ 사본에서만 일하고 PR.md에서 멈춘다. 커밋·머지·배포는 아래 명령으로 사람이 한다.
ai-fix: ## 승인된 사건 하나를 제한된 Claude Code 수정 세션에 넘긴다. TASK=login|price|owner INCIDENT=번호
	@$(PYTHON) tools/ai_fix.py start --task $(TASK) --incident-id $(INCIDENT)

ai-fix-diff: ## 수정 사본의 변경과 자동 리뷰 힌트를 본다. TASK=…
	@$(PYTHON) tools/ai_fix.py diff --task $(TASK)

ai-fix-test: ## 수정 사본의 시험을 다시 돌린다(Claude 없이). TASK=…
	@$(PYTHON) tools/ai_fix.py test --task $(TASK)

ai-fix-verify: ## 사본으로 후보를 만들어 포트 19186에서 과제 검증기를 돌린다. TASK=…
	@$(PYTHON) tools/ai_fix.py verify --task $(TASK)

ai-pr: ## 사본 브랜치를 커밋하고 로컬 PR 파일을 만든다. GITHUB=1이면 원격에 PR. TASK=…
	@$(PYTHON) tools/ai_fix.py pr --task $(TASK) $(if $(GITHUB),--github,)

ai-merge: ## 사람이 리뷰한 브랜치를 사본의 main에 머지한다. TASK=…
	@$(PYTHON) tools/ai_fix.py merge --task $(TASK)

ai-fix-deploy: ## 머지된 main을 본 쇼핑몰(또는 설정)에 배포한다. TASK=…
	@$(PYTHON) tools/ai_fix.py deploy --task $(TASK)

ai-fix-check: ## 배포 뒤 본 쇼핑몰에서 같은 입력으로 재시험한다. TASK=price|owner
	@$(PYTHON) tools/ai_fix.py check --task $(TASK)

ai-fix-rollback: ## 배포를 되돌려 기본(레드) 상태로. TASK=…
	@$(PYTHON) tools/ai_fix.py rollback --task $(TASK)
