"""Accept a baseline only after five complete, comparable 30-minute runs."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import statistics

from run_stages import HERE


MEASUREMENTS = HERE / "measurements"
REQUIRED_SEEDS = {1: 1426, 2: 1426, 3: 1426, 4: 1711, 5: 1859}


def band(values: list[float | int]) -> dict:
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    ordered = sorted(values)
    upper_index = min(len(ordered) - 1, int(0.95 * len(ordered)))
    return {"median": round(median, 4), "mad": round(mad, 4),
            "suggested_lower": round(max(0, median - 3 * mad), 4),
            "suggested_upper": round(median + 3 * mad, 4),
            "empirical_p95": ordered[upper_index],
            "minimum": min(values), "maximum": max(values),
            "zero_mad_note": "MAD=0이면 제안 상한만으로 경보를 정하지 말고 관측 최대값·최소 건수와 함께 검토"
            if mad == 0 else None}


def load_runs() -> tuple[list[dict], list[str]]:
    grouped: dict[int, list[dict]] = {}
    issues = []
    for path in sorted(MEASUREMENTS.glob("baseline-candidate-r*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("kind") != "baseline_candidate":
            continue
        data["_file"] = path.name
        grouped.setdefault(data["run"], []).append(data)
    runs = []
    for number, seed in REQUIRED_SEEDS.items():
        matches = grouped.get(number, [])
        if len(matches) != 1:
            issues.append(f"run {number}: complete candidate file count is {len(matches)}, expected 1")
            continue
        data = matches[0]
        if data["seed"] != seed:
            issues.append(f"run {number}: seed {data['seed']} != {seed}")
        if data["duration_target_seconds"] != 1800:
            issues.append(f"run {number}: target duration is not 1800 seconds")
        if data.get("duration_actual_seconds", 0) < 1799:
            issues.append(f"run {number}: actual duration is shorter than 1799 seconds")
        if data.get("duration_actual_seconds", 0) > 1830:
            issues.append(f"run {number}: actual duration exceeded 1830 seconds")
        if data.get("overall", {}).get("requests") != 3600 or len(data.get("samples", [])) != 3600:
            issues.append(f"run {number}: request/sample count is not 3600")
        if data.get("unexpected_results") != 0:
            issues.append(f"run {number}: unexpected route/status/body results: {data.get('unexpected_results')}")
        if len(data.get("auth_refresh_at_seconds", [])) != 2:
            issues.append(f"run {number}: expected two token refreshes during the 30-minute run")
        if data.get("auth_refresh_interval_seconds") != 600:
            issues.append(f"run {number}: token refresh interval is not 600 seconds")
        if len(data.get("five_minute_bins", [])) != 6:
            issues.append(f"run {number}: five-minute bin count is not 6")
        elif any(not 590 <= bin_["requests"] <= 610 for bin_ in data["five_minute_bins"]):
            issues.append(f"run {number}: a five-minute bin is outside 590..610 requests")
        if set(data.get("loki", {})) != {"security", "error"}:
            issues.append(f"run {number}: security/error Loki queries are missing")
        if any("query_error" in value or value.get("possibly_truncated") for value in data.get("loki", {}).values()):
            issues.append(f"run {number}: Loki query failed or reached its limit")
        runs.append(data)
    if set(grouped) - set(REQUIRED_SEEDS):
        issues.append(f"unexpected run IDs: {sorted(set(grouped) - set(REQUIRED_SEEDS))}")
    if len({data["target_rps"] for data in runs}) > 1:
        issues.append("target RPS differs across runs")
    if len({json.dumps(data["traffic_weights_per_1000"], sort_keys=True) for data in runs}) > 1:
        issues.append("traffic mix differs across runs")
    if runs and {data["target_rps"] for data in runs} != {2.0}:
        issues.append("target RPS is not 2.0")
    if len({data.get("environment_record") for data in runs}) > 1:
        issues.append("environment record differs across runs; review comparability")
    return runs, issues


def main() -> int:
    runs, issues = load_runs()
    complete = len(runs) == len(REQUIRED_SEEDS) and not issues
    run_p95 = [data["overall"]["p95_ms"] for data in runs]
    latency_ratio = round(max(run_p95) / min(run_p95), 3) if run_p95 and min(run_p95) > 0 else None
    # A shared teaching laptop can keep the HTTP mix reproducible while host
    # contention makes a latency alert threshold misleading. Report that split.
    latency_accepted = complete and latency_ratio is not None and latency_ratio <= 2
    kind = ("incomplete_baseline" if not complete else
            "accepted_baseline" if latency_accepted else "accepted_http_baseline_latency_unstable")
    output = {"kind": kind,
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "required_runs": 5, "completed_runs": len(runs), "issues": issues,
              "run_files": [data["_file"] for data in runs],
              "metric_status": {"http_counts_and_rates": "accepted" if complete else "incomplete",
                                "latency_p95": "accepted" if latency_accepted else
                                "unstable_not_for_alert_threshold" if complete else "incomplete"},
              "run_p95_spread_ratio": latency_ratio,
              "latency_stability_rule": "max(run p95) / min(run p95) <= 2 (teaching-lab gate)"}
    if runs:
        five_minute = [b for data in runs for b in data["five_minute_bins"]]
        output["five_minute_bands"] = {
            "requests": band([b["requests"] for b in five_minute]),
            "http_5xx_count": band([b["http_5xx"] for b in five_minute]),
            "http_5xx_rate_pct": band([b["http_5xx_rate_pct"] for b in five_minute]),
            "http_4xx_count": band([b["http_4xx"] for b in five_minute]),
            "foreign_order_denied_route_count": band([b["route_counts"].get("foreign_order_denied", 0)
                                                      for b in five_minute]),
            "p95_ms": band([b["p95_ms"] for b in five_minute if b["p95_ms"] is not None]),
            "p99_ms": band([b["p99_ms"] for b in five_minute if b["p99_ms"] is not None]),
        }
        output["run_5xx_rates_pct"] = [data["overall"]["http_5xx_rate_pct"] for data in runs]
        output["run_p95_ms"] = run_p95
        statuses = Counter()
        for data in runs:
            statuses.update({key: value for key, value in data["overall"]["http_status_counts"].items()})
        output["all_http_status_counts"] = dict(sorted(statuses.items()))
        output["same_seed_rate_spread_pct_points"] = (
            round(max(output["run_5xx_rates_pct"][:3]) - min(output["run_5xx_rates_pct"][:3]), 4)
            if len(runs) >= 3 and [r["run"] for r in runs[:3]] == [1, 2, 3] else None)
    target = MEASUREMENTS / "baseline-summary.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "kind": output["kind"], "completed_runs": len(runs),
                      "issues": issues, "metric_status": output["metric_status"],
                      "run_p95_spread_ratio": latency_ratio,
                      "five_minute_bands": output.get("five_minute_bands")}, ensure_ascii=False))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
