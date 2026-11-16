"""Small deterministic normal-traffic collector; explicitly not a 30-minute baseline."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import random
import time

from run_stages import HERE, login, request, loki_entries


def percentile(values: list[float], fraction: float):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--rps", type=float, default=2)
    parser.add_argument("--seed", type=int, default=1426)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 1800 or not 0 < args.rps <= 20:
        raise ValueError("seconds must be 1..1800 and rps 0..20")
    rng = random.Random(args.seed)
    alice = login("alice")
    endpoints = [("home", "/home/content", ""),
                 ("product", "/product/detail/202", ""),
                 ("own_order", "/order/detail/9001", alice)]
    started = datetime.now(timezone.utc)
    start_ns = time.time_ns()
    start = time.monotonic()
    rows = []
    index = 0
    while True:
        due = index / args.rps
        if due >= args.seconds:
            break
        pause = start + due - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        name, path, token = rng.choice(endpoints)
        result = request("GET", path, token)
        rows.append({"second": round(time.monotonic() - start, 3), "endpoint": name,
                     "http_status": result["http"], "body_code": result["body"].get("code"),
                     "latency_ms": result["latency_ms"]})
        index += 1
    end_ns = time.time_ns()
    elapsed = time.monotonic() - start
    time.sleep(3)
    try:
        denied = len(loki_entries('{app="mall-portal",stream="security",event="access.denied.not_owner"}', start_ns, end_ns))
        errors = len(loki_entries('{app="mall-portal",level="ERROR"}', start_ns, end_ns))
    except Exception:
        denied = errors = None
    status_counts = Counter(str(r["http_status"]) for r in rows)
    body_counts = Counter(str(r["body_code"]) for r in rows)
    latencies = [r["latency_ms"] for r in rows]
    bins = []
    for beginning in range(0, args.seconds, 10):
        subset = [r for r in rows if beginning <= r["second"] < beginning + 10]
        bins.append({"seconds": [beginning, beginning + 10], "requests": len(subset),
                     "http_5xx": sum(r["http_status"] >= 500 for r in subset),
                     "p95_ms": percentile([r["latency_ms"] for r in subset], .95)})
    summary = {"kind": "traffic_pilot_not_baseline", "started_utc": started.isoformat(),
               "duration_target_seconds": args.seconds, "duration_actual_seconds": round(elapsed, 2),
               "seed": args.seed, "target_rps": args.rps, "total_requests": len(rows),
               "http_status_counts": dict(status_counts), "body_code_counts": dict(body_counts),
               "p50_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95),
               "p99_ms": percentile(latencies, .99), "loki_denied_events": denied,
               "loki_error_lines": errors, "ten_second_bins": bins,
               "environment_record": max((HERE / "measurements").glob("environment-*.json")).name,
               "samples": rows,
               "limitation": "One short test run; not the 30-minute x 5 baseline in the measurement plan."}
    out = HERE / "measurements" / f"traffic-pilot-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"file": str(out), **{k: summary[k] for k in ("total_requests", "http_status_counts", "body_code_counts", "p50_ms", "p95_ms", "p99_ms", "loki_denied_events", "loki_error_lines")}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
