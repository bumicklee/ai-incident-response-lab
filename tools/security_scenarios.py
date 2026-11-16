"""Reproduce bounded authentication and object-access patterns with synthetic users.

Run only after baseline collection. No raw password, token, or response body is
written to measurements. The login limiter's 10-minute Redis window remains in
effect after execution; repeat runs must wait for it to expire.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import time
import uuid

from run_stages import HERE, login, loki_entries, request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("normal", "spray", "idor", "shared-links"), required=True)
    parser.add_argument("--interval-seconds", type=int, default=0,
                        help="shared-links 요청 사이 대기. 1분 규칙과 10분 규칙 비교에는 150초 사용")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.interval_seconds <= 240:
        parser.error("--interval-seconds must be between 0 and 240")
    if args.interval_seconds and args.scenario != "shared-links":
        parser.error("--interval-seconds is only for shared-links")
    plan = {
        "normal": "가짜 Alice 비밀번호 오타 1회와 정상 로그인 1회",
        "spray": "존재하지 않는 가짜 계정 10개 실패 후 별도 가짜 계정 6회 실패",
        "idor": "가짜 Bob의 본인 주문 9003 조회와 다른 회원 주문 5개 조회",
        "shared-links": "정상 역할의 가짜 Bob이 가족에게 받은 오래된 주문 링크 3개 조회",
    }[args.scenario]
    if args.dry_run:
        print(json.dumps({"kind": "dry_run_no_requests", "scenario": args.scenario,
                          "plan": plan, "interval_seconds": args.interval_seconds,
                          "real_accounts_or_credentials_used": False}, ensure_ascii=False))
        return 0

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start = datetime.now(timezone.utc)
    start_ns = time.time_ns()
    rows = []

    def send(name: str, path: str, body=None, token: str = "", target: str = "") -> dict:
        trace = "sre-sec-" + uuid.uuid4().hex[:12]
        result = request("POST" if body is not None else "GET", path, token=token, body=body, trace=trace)
        rows.append({"step": name, "http_status": result["http"],
                     "body_code": result["body"].get("code"), "trace_id": trace,
                     "target": target, "latency_ms": result["latency_ms"]})
        return result

    if args.scenario == "normal":
        wrong = send("single_login_typo", "/sso/login",
                     {"username": "alice", "password": "lab-wrong-password"})
        correct = send("login_after_typo", "/sso/login",
                       {"username": "alice", "password": "daon-pass-1!"})
        if wrong["body"].get("code") == 200 or not (correct["body"].get("data") or {}).get("token"):
            raise SystemExit("정상 오타·재로그인 결과가 예상과 다릅니다")
    elif args.scenario == "spray":
        # Distinct fake identifiers exercise the existing failed_burst counter.
        for index in range(10):
            result = send("different_fake_account_failure", "/sso/login",
                          {"username": f"sre-spray-{run_id}-{index}", "password": "lab-wrong-password"})
            if (result["body"].get("data") or {}).get("token"):
                raise SystemExit("가짜 계정 실패 요청이 성공했습니다")
        # A separate account exercises the per-account limiter without using a real user.
        for index in range(6):
            result = send("same_fake_account_failure", "/sso/login",
                          {"username": f"sre-limit-{run_id}", "password": "lab-wrong-password"})
            if (result["body"].get("data") or {}).get("token"):
                raise SystemExit("가짜 계정 실패 요청이 성공했습니다")
        if not any(row["http_status"] == 429 for row in rows):
            raise SystemExit("429 속도 제한을 관측하지 못했습니다. 스위치·Redis 창을 확인하세요")
    elif args.scenario == "idor":
        bob = login("bob")
        own = send("own_order_allowed", "/order/detail/9003", token=bob, target="order:9003")
        if (own["http"], own["body"].get("code")) != (200, 200):
            raise SystemExit("Bob 본인 주문이 정상 조회되지 않습니다")
        for order_id in (9001, 9002, 9004, 9005, 9006):
            denied = send("foreign_order_denied", f"/order/detail/{order_id}",
                          token=bob, target=f"order:{order_id}")
            if (denied["http"], denied["body"].get("code")) != (200, 500):
                raise SystemExit(f"타인 주문 {order_id}가 예상한 200/500 거부가 아닙니다")
    else:
        bob = login("bob")
        for index, order_id in enumerate((9001, 9002, 9004)):
            if index and args.interval_seconds:
                print(f"shared-links: {index + 1}/3 요청 전 {args.interval_seconds}초 대기", flush=True)
                time.sleep(args.interval_seconds)
            denied = send("old_family_link_denied", f"/order/detail/{order_id}",
                          token=bob, target=f"order:{order_id}")
            if (denied["http"], denied["body"].get("code")) != (200, 500):
                raise SystemExit(f"가족에게 받은 링크 {order_id}의 200/500 거부를 확인하지 못했습니다")

    end_ns = time.time_ns()
    time.sleep(4)
    events = loki_entries('{app="mall-portal",stream="security"}', start_ns, end_ns)
    if len(events) == 1000:
        raise SystemExit("Loki 조회가 1000건 상한에 닿아 사건 수를 확정할 수 없습니다")
    counts = Counter(row["labels"].get("event", "unlabelled") for row in events)
    output = {"kind": "synthetic_security_scenario", "scenario": args.scenario,
              "ground_truth": "normal" if args.scenario == "shared-links" else "scenario_only",
              "interval_seconds": args.interval_seconds,
              "run_id": run_id, "started_utc": start.isoformat(),
              "ended_utc": datetime.now(timezone.utc).isoformat(),
              "requests": rows, "loki_event_counts": dict(sorted(counts.items())),
              "passwords_tokens_and_raw_logs_saved": False,
              "limitations": ["One bounded synthetic run, not an attack detection rate.",
                              "The login limiter keeps Redis counters for up to 10 minutes."]}
    target = HERE / "measurements" / f"security-{args.scenario}-{run_id}.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "scenario": args.scenario,
                      "http_statuses": dict(Counter(r["http_status"] for r in rows)),
                      "loki_event_counts": output["loki_event_counts"]}, ensure_ascii=False))
    required = {"normal": "auth.login.failed", "spray": "auth.login.failed_burst",
                "idor": "access.denied.not_owner", "shared-links": "access.denied.not_owner"}[args.scenario]
    return 0 if counts[required] else 2


if __name__ == "__main__":
    raise SystemExit(main())
