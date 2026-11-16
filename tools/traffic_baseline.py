"""Collect reproducible routine traffic without mistaking one run for a baseline.

All requests use synthetic accounts. Only status, timing, and trace IDs are saved;
bearer tokens and response bodies stay in memory. A 30-minute run is a candidate.
The accepted baseline requires three runs with seed 1426 and two other seeds.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import random
import time
import uuid

from run_stages import HERE, login, loki_entries, request


ROUTES = (
    # name, path, actor, weight per 1,000; rare errors are routine noise.
    ("home", "/home/content", "public", 420),
    ("product", "/product/detail/202", "public", 350),
    ("own_order", "/order/detail/9001", "alice", 210),
    ("unauth_order_401", "/order/detail/9001", "public", 10),
    ("unknown_route_404", "/missing-sre-page", "alice", 6),
    ("unknown_product_500", "/product/detail/999999", "alice", 2),
    ("foreign_order_denied", "/order/detail/9001", "bob", 2),
)
EXPECTED = {
    "home": (200, 200), "product": (200, 200), "own_order": (200, 200),
    "unauth_order_401": (401, 401), "unknown_route_404": (404, None),
    "unknown_product_500": (500, 500), "foreign_order_denied": (200, 500),
}


def percentile(values: list[float], part: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * part
    lo, hi = math.floor(index), math.ceil(index)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo), 1)


def metrics(rows: list[dict]) -> dict:
    statuses = Counter(str(r["http_status"]) for r in rows)
    bodies = Counter(str(r["body_code"]) for r in rows)
    routes = Counter(r["route"] for r in rows)
    latencies = [r["latency_ms"] for r in rows]
    return {
        "requests": len(rows),
        "http_status_counts": dict(sorted(statuses.items())),
        "body_code_counts": dict(sorted(bodies.items())),
        "route_counts": dict(sorted(routes.items())),
        "http_5xx": sum(r["http_status"] >= 500 for r in rows),
        "http_5xx_rate_pct": round(100 * sum(r["http_status"] >= 500 for r in rows) / len(rows), 4) if rows else None,
        "http_4xx": sum(400 <= r["http_status"] < 500 for r in rows),
        "p50_ms": percentile(latencies, .5),
        "p95_ms": percentile(latencies, .95),
        "p99_ms": percentile(latencies, .99),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=int, default=1800)
    ap.add_argument("--rps", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--auth-refresh-seconds", type=int, default=600)
    ap.add_argument("--purpose", choices=("baseline", "post_green_observation"), default="baseline")
    args = ap.parse_args()
    if (not 10 <= args.seconds <= 1800 or not 0 < args.rps <= 10
            or not 1 <= args.run <= 5 or not 2 <= args.auth_refresh_seconds <= 600):
        ap.error("seconds must be 10..1800, rps 0..10, run 1..5, auth refresh 2..600")
    if args.seed is None:
        args.seed = {1: 1426, 2: 1426, 3: 1426, 4: 1711, 5: 1859}[args.run]
    if sum(item[3] for item in ROUTES) != 1000:
        raise AssertionError("route weights must total 1,000")
    if args.purpose == "post_green_observation" and args.seconds != 900:
        ap.error("post_green_observation requires exactly 900 seconds")

    alice, bob = login("alice"), login("bob")
    tokens = {"public": "", "alice": alice, "bob": bob}
    rng = random.Random(args.seed)
    started = datetime.now(timezone.utc)
    start_ns = time.time_ns()
    clock_start = time.monotonic()
    rows: list[dict] = []
    auth_refresh_at_seconds: list[float] = []
    next_auth_refresh = args.auth_refresh_seconds
    partial_prefix = "post-green-partial" if args.purpose == "post_green_observation" else "baseline-partial"
    partial_output = HERE / "measurements" / f"{partial_prefix}-r{args.run}-{started.strftime('%Y%m%dT%H%M%SZ')}.json"

    def save_partial(failure_type: str | None = None) -> None:
        """Preserve evidence without making an interrupted run a baseline candidate."""
        elapsed = round(time.monotonic() - clock_start, 2)
        partial = {
            "kind": ("post_green_failed_partial_not_final" if failure_type else "post_green_progress_not_final")
                    if args.purpose == "post_green_observation" else
                    ("baseline_failed_partial_not_accepted" if failure_type else "baseline_progress_not_accepted"),
            "run": args.run, "started_utc": started.isoformat(),
            "duration_target_seconds": args.seconds, "duration_actual_seconds": elapsed,
            "target_rps": args.rps, "seed": args.seed,
            "auth_refresh_interval_seconds": args.auth_refresh_seconds,
            "auth_refresh_at_seconds": auth_refresh_at_seconds,
            "failure_type": failure_type,
            "synthetic_accounts_only": True,
            "overall": metrics(rows),
            "samples": rows,
            "limitations": ["Partial run; never use as a completed observation or accepted baseline."],
        }
        partial_output.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    index = 0
    next_progress_seconds = 300
    while index / args.rps < args.seconds:
        due = index / args.rps
        pause = clock_start + due - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        elapsed_before_request = time.monotonic() - clock_start
        if elapsed_before_request >= next_auth_refresh:
            try:
                tokens["alice"], tokens["bob"] = login("alice"), login("bob")
            except Exception as exc:
                save_partial(type(exc).__name__)
                raise
            auth_refresh_at_seconds.append(round(time.monotonic() - clock_start, 2))
            next_auth_refresh += args.auth_refresh_seconds
        name, path, actor, _ = rng.choices(ROUTES, weights=[r[3] for r in ROUTES], k=1)[0]
        trace = "sre-base-" + uuid.uuid4().hex[:16]
        try:
            result = request("GET", path, tokens[actor], trace=trace)
        except Exception as exc:
            save_partial(type(exc).__name__)
            raise
        status = result["http"]
        rows.append({
            "elapsed_seconds": round(time.monotonic() - clock_start, 3),
            "route": name,
            "http_status": status,
            "body_code": result["body"].get("code"),
            "latency_ms": result["latency_ms"],
            # Keep only unexpected or denied requests for later raw-log lookup.
            "trace_id": trace if status >= 400 or result["body"].get("code") not in (None, 200) else None,
            "expected_result": (status, result["body"].get("code")) == EXPECTED[name],
        })
        index += 1
        if index / args.rps >= next_progress_seconds and next_progress_seconds <= args.seconds:
            save_partial()
            print(json.dumps({"run": args.run, "elapsed_target_seconds": int(index / args.rps),
                              "requests": index, "http_5xx_so_far": sum(r["http_status"] >= 500 for r in rows)},
                             ensure_ascii=False), flush=True)
            next_progress_seconds += 300
    elapsed = round(time.monotonic() - clock_start, 2)
    end_ns = time.time_ns()
    time.sleep(3)  # give Alloy time to deliver the last entries
    loki = {}
    for label, query in {
        "security": '{app="mall-portal",stream="security"}',
        "error": '{app="mall-portal",level="ERROR"}',
    }.items():
        try:
            entries = loki_entries(query, start_ns, end_ns)
            by_event = Counter(e["labels"].get("event", "unlabelled") for e in entries)
            loki[label] = {"count": len(entries), "event_counts": dict(sorted(by_event.items())),
                           "query_limit": 1000, "possibly_truncated": len(entries) == 1000}
        except Exception as exc:
            loki[label] = {"query_error": type(exc).__name__}

    bins = []
    for begin in range(0, args.seconds, 300):
        subset = [r for r in rows if begin <= r["elapsed_seconds"] < begin + 300]
        bins.append({"seconds": [begin, min(begin + 300, args.seconds)], **metrics(subset)})
    environments = sorted((HERE / "measurements").glob("environment-*.json"))
    summary = {
        "kind": "post_green_observation" if args.purpose == "post_green_observation" else
                "baseline_candidate" if args.seconds == 1800 else "baseline_tool_check_not_baseline",
        "run": args.run, "started_utc": started.isoformat(), "duration_target_seconds": args.seconds,
        "duration_actual_seconds": elapsed, "target_rps": args.rps, "seed": args.seed,
        "auth_refresh_interval_seconds": args.auth_refresh_seconds,
        "auth_refresh_at_seconds": auth_refresh_at_seconds,
        "unexpected_results": sum(not r["expected_result"] for r in rows),
        "synthetic_accounts_only": True, "traffic_weights_per_1000": {r[0]: r[3] for r in ROUTES},
        "overall": metrics(rows), "five_minute_bins": bins, "loki": loki,
        "environment_record": environments[-1].name if environments else None,
        "samples": rows,
        "limitations": [
            ("Post-green switch observation, not a PR/Actions deployment or accepted baseline."
             if args.purpose == "post_green_observation" else
             "One 30-minute run is not an accepted baseline; summarize five runs with the required seeds."),
            "The deliberately rare 500 uses unknown product 999999, not the order incident.",
            "HTTP 403 and 429 are reported as zero if the app did not emit them; body code is separate.",
            "Emulator delivery times cannot establish physical phone sleep/doze latency.",
        ],
    }
    prefix = "post-green-observation" if args.purpose == "post_green_observation" else f"baseline-candidate-r{args.run}"
    output = HERE / "measurements" / f"{prefix}-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(output), "kind": summary["kind"], "overall": summary["overall"],
                      "loki": loki}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
