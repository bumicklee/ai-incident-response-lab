#!/usr/bin/env python3
"""secure-kit login unit: send normal typos, a brute force on one account, and credential stuffing.

    make up-kit            # turns on @RateLimited("login") (compose.kit.yaml)
    python3 tools/kit_login_scenario.py

Only fake accounts and wrong passwords are used. Every request carries its own X-Trace-Id.
The result file lists each request (HTTP, body code, Retry-After) and the Loki event counts:
auth.login.failed comes from the app, auth.login.rate_limited from secure-kit.
Bob's account stays limited for up to 10 minutes after this run (the kit's per-account window).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter

from run_stages import HERE, LOKI, PORTAL, loki_entries

PASSWORD = "daon-pass-1!"


def login(username: str, password: str, phase: str) -> dict:
    trace = "sre-kit-" + uuid.uuid4().hex[:12]
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(PORTAL + "/sso/login", data=data, method="POST",
                                 headers={"X-Trace-Id": trace, "Content-Type": "application/x-www-form-urlencoded"})
    try:
        response = urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        status, retry = response.status, response.headers.get("Retry-After")
    try:
        code = json.loads(raw).get("code")
    except ValueError:
        code = None
    # the account name is kept only as a role label, never the password
    return {"phase": phase, "account": username, "http": status, "code": code, "retry_after": retry, "trace": trace}


def main() -> int:
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start_ns = time.time_ns()
    rows = []
    # A. a normal member mistypes once, then logs in
    rows.append(login("alice", "daon-pass-1?", "normal_typo"))
    rows.append(login("alice", PASSWORD, "normal_after_typo"))
    # B. brute force: seven wrong passwords on one account, then the right one
    for n in range(7):
        rows.append(login("bob", f"guess-{n:02d}", "brute_force"))
    rows.append(login("bob", PASSWORD, "brute_force_right_password"))
    # C. credential stuffing: one wrong guess each on many different accounts from the same IP
    for n in range(22):
        rows.append(login(f"kit-guess-{n:02d}", "leaked-pass-1", "stuffing"))
    # D. after the attacks: a normal member with no failures logs in from the same IP
    rows.append(login("alice", PASSWORD, "normal_after_attacks"))
    time.sleep(8)  # Alloy → Loki
    end_ns = time.time_ns()
    events = loki_entries('{app="mall-portal",stream="security"} |~ "auth.login"', start_ns - 5_000_000_000, end_ns)
    counts = Counter()
    for row in events:
        try:
            counts[json.loads(row["line"]).get("event")] += 1
        except ValueError:
            pass
    summary = {}
    for row in rows:
        key = f'{row["phase"]} HTTP {row["http"]}'
        summary[key] = summary.get(key, 0) + 1
    first_429 = {phase: next((i + 1 for i, r in enumerate([x for x in rows if x["phase"] == phase]) if r["http"] == 429), None)
                 for phase in ("brute_force", "stuffing")}
    out = HERE / "measurements" / f"kit-login-{run_id}.json"
    result = {"run_id": run_id, "portal": PORTAL, "loki": LOKI, "kit_limits": {"per_account": "5/10m", "per_ip": os.environ.get("KIT_LOGIN_PER_IP", "100/10m")},
              "summary": summary, "first_429_attempt": first_429, "loki_event_counts": dict(counts), "requests": rows,
              "notes": ["Accounts are fake lab members; passwords are wrong guesses.",
                        "Bob stays limited for up to 10 minutes; restart mall-portal to clear the in-memory counter."]}
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(out.relative_to(HERE)), "summary": summary, "first_429_attempt": first_429,
                      "loki_event_counts": dict(counts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
