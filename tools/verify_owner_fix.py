"""Check the return-ownership fix over HTTP with fake accounts, then delete the test return rows.

    LAB_PORTAL_PORT=19186 python3 tools/verify_owner_fix.py              # isolated candidate (make ai-fix-verify)
    VERIFY_ANY_PORT=1 LAB_PORTAL_PORT=18085 python3 tools/verify_owner_fix.py  # live shop after deploy (make ai-fix-check)

Pass = bob's return for order 9002 (not his) is refused, Alice's own return for 9001 is accepted, and the refusal
left an access.denied.not_owner event in Loki. Same inputs as run_stages.py, with its own cleanup marker.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import subprocess
import time
import uuid

from run_stages import HERE, LOKI, PORTAL, loki_entries, login, request


def cleanup(marker: str) -> list[int]:
    if not re.fullmatch(r"SRE-AIFIX-[0-9a-f]{12}", marker):
        raise ValueError("unexpected marker; refusing cleanup")
    sql = (f"SELECT COUNT(*) FROM oms_order_return_apply WHERE description = '{marker}';\n"
           f"DELETE FROM oms_order_return_apply WHERE description = '{marker}';\n"
           f"SELECT COUNT(*) FROM oms_order_return_apply WHERE description = '{marker}';\n")
    result = subprocess.run(["docker", "compose", "-f", str(HERE / "compose.yaml"), "exec", "-T", "mysql",
                             "sh", "-c", 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" mall -N'],
                            input=sql, text=True, capture_output=True, check=True)
    return [int(x) for x in result.stdout.split()]


def main() -> int:
    if PORTAL != "http://127.0.0.1:19186" and os.environ.get("VERIFY_ANY_PORT") != "1":
        raise SystemExit("owner verifier only accepts LAB_PORTAL_PORT=19186 unless VERIFY_ANY_PORT=1")
    for _ in range(45):
        try:
            if request("GET", "/home/content")["http"] == 200:
                break
        except OSError:
            pass
        time.sleep(2)
    else:
        raise SystemExit("portal did not become HTTP ready within 90 seconds")
    started = datetime.now(timezone.utc)
    started_ns = time.time_ns()
    alice, bob = login("alice"), login("bob")
    marker = "SRE-AIFIX-" + uuid.uuid4().hex[:12]
    ret = {"orderId": 9002, "productId": 27, "orderSn": "FAKE-SN", "memberUsername": "bob",
           "returnName": "테스트", "returnPhone": "010-0000-0000", "productCount": 1,
           "reason": "단순 변심", "description": marker}
    trace_foreign = "sre-aifix-owner-" + uuid.uuid4().hex[:8]
    try:
        foreign = request("POST", "/returnApply/create", bob, ret, trace_foreign)
        own = request("POST", "/returnApply/create", alice, {**ret, "orderId": 9001, "memberUsername": "alice"})
    finally:
        counts = cleanup(marker)
    time.sleep(4)
    try:
        denied = loki_entries('{app=~"mall-portal(-candidate)?",stream="security",event="access.denied.not_owner"} |= "' + trace_foreign + '"',
                              started_ns - 5_000_000_000, time.time_ns())
    except Exception as exc:  # noqa: BLE001
        denied = None
        print(json.dumps({"loki_query_error": type(exc).__name__}))
    checks = {
        "foreign_return_refused": foreign["body"].get("code") != 200,
        "own_return_accepted": (own["http"], own["body"].get("code")) == (200, 200),
        "denied_event_logged": bool(denied),
        "test_rows_cleaned": len(counts) == 2 and counts[1] == 0,
    }
    record = {"kind": "owner_fix_http_verification", "portal": PORTAL, "loki": LOKI,
              "started_utc": started.isoformat(), "ended_utc": datetime.now(timezone.utc).isoformat(),
              "foreign_return": {"http": foreign["http"], "code": foreign["body"].get("code"), "trace_id": trace_foreign},
              "own_return": {"http": own["http"], "code": own["body"].get("code")},
              "rows_with_marker_before_after_cleanup": counts, "checks": checks, "passed": all(checks.values())}
    target = HERE / "measurements" / f"owner-fix-http-{started:%Y%m%dT%H%M%SZ}.json"
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target.relative_to(HERE)), **{k: record[k] for k in ("foreign_return", "own_return", "checks")}},
                     ensure_ascii=False, indent=2))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
