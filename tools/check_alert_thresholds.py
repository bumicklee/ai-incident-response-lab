"""Check each Grafana 1-minute teaching threshold against five quiet runs.

This measures historical log timestamps in every sliding 60-second window. It
does not fire Grafana alerts; a later incident injection tests the live path.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from run_stages import HERE, loki_entries


QUERIES = {
    "order_error_unhandled": ('{app="mall-portal"} |= "error.unhandled"', 2),
    "foreign_order_denied": ('{app="mall-portal",stream="security",event="access.denied.not_owner"}', 2),
    "login_failed_burst": ('{app="mall-portal",stream="security",event="auth.login.failed_burst"}', 0),
}


def maximum_in_window(timestamps: list[int], width_ns: int = 60_000_000_000) -> int:
    ordered = sorted(timestamps)
    left = 0
    maximum = 0
    for right, stamp in enumerate(ordered):
        while stamp - ordered[left] > width_ns:
            left += 1
        maximum = max(maximum, right - left + 1)
    return maximum


def main() -> int:
    measurements = HERE / "measurements"
    rows = []
    for run in range(1, 6):
        paths = [path for path in measurements.glob(f"baseline-candidate-r{run}-*.json")
                 if json.loads(path.read_text(encoding="utf-8")).get("kind") == "baseline_candidate"]
        if len(paths) != 1:
            raise SystemExit(f"run {run}: expected exactly one candidate")
        candidate = json.loads(paths[0].read_text(encoding="utf-8"))
        start_ns = int(datetime.fromisoformat(candidate["started_utc"]).timestamp() * 1e9)
        end_ns = start_ns + int((candidate["duration_actual_seconds"] + 1) * 1e9)
        for name, (query, threshold) in QUERIES.items():
            events = loki_entries(query, start_ns, end_ns)
            if len(events) >= 1000:
                raise SystemExit(f"run {run}, {name}: Loki query may be truncated")
            maximum = maximum_in_window([int(event["timestamp_ns"]) for event in events])
            rows.append({"run": run, "event": name, "count": len(events),
                         "max_rolling_60s": maximum, "alert_if_greater_than": threshold,
                         "would_fire": maximum > threshold})
    output = {"kind": "historical_alert_threshold_check",
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "window_seconds": 60, "rows": rows,
              "normal_run_would_fire": any(row["would_fire"] for row in rows),
              "limitation": "Historical timestamps only; Grafana evaluation and webhook delivery are tested separately."}
    target = measurements / "alert-threshold-check.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "normal_run_would_fire": output["normal_run_would_fire"],
                      "maxima": {name: max(row["max_rolling_60s"] for row in rows if row["event"] == name)
                                 for name in QUERIES}}, ensure_ascii=False))
    return 0 if not output["normal_run_would_fire"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
