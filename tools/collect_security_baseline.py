"""Read completed baseline windows from Loki and count security events per 5 minutes.

Run after baseline-all. This tool reads Loki; it does not inject traffic or alter
the lab services. It stores event names and counts, never raw log lines.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json

from run_stages import HERE, loki_entries


MEASUREMENTS = HERE / "measurements"
QUERY = '{app="mall-portal",stream="security"}'
WINDOW_SECONDS = 300
EXPECTED_SECONDS = 1800


def candidate_for(run: int):
    matches = []
    for path in sorted(MEASUREMENTS.glob(f"baseline-candidate-r{run}-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("kind") == "baseline_candidate":
            matches.append((path, data))
    if len(matches) != 1:
        raise ValueError(f"run {run}: expected one complete candidate, found {len(matches)}")
    return matches[0]


def collect(run: int) -> dict:
    source, data = candidate_for(run)
    if data.get("duration_target_seconds") != EXPECTED_SECONDS or data.get("overall", {}).get("requests") != 3600:
        raise ValueError(f"run {run}: candidate is not a complete 30-minute, 3,600-request run")
    reference = data.get("loki", {}).get("security", {})
    if "count" not in reference or reference.get("possibly_truncated"):
        raise ValueError(f"run {run}: security reference is missing or truncated")

    started_ns = int(datetime.fromisoformat(data["started_utc"]).timestamp() * 1_000_000_000)
    # The candidate used time.time_ns() just after started_utc. One extra second
    # compensates for the rounded actual duration and is checked below.
    end_ns = started_ns + int((float(data["duration_actual_seconds"]) + 1) * 1_000_000_000)
    entries = loki_entries(QUERY, started_ns, end_ns)
    if len(entries) == 1000:
        raise ValueError(f"run {run}: Loki query reached the 1000-entry limit")

    bins = [Counter() for _ in range(EXPECTED_SECONDS // WINDOW_SECONDS)]
    out_of_window = 0
    for entry in entries:
        elapsed = (int(entry["timestamp_ns"]) - started_ns) / 1_000_000_000
        index = int(elapsed // WINDOW_SECONDS)
        if not (0 <= index < len(bins)):
            out_of_window += 1
            continue
        bins[index][entry["labels"].get("event", "unlabelled")] += 1
    all_events = Counter()
    for bin_ in bins:
        all_events.update(bin_)
    if out_of_window or len(entries) != reference["count"] or dict(all_events) != reference["event_counts"]:
        raise ValueError(
            f"run {run}: 5-minute Loki counts disagree with the candidate "
            f"(queried={len(entries)}, reference={reference['count']}, outside={out_of_window})"
        )

    output = {
        "kind": "security_five_minute_candidate",
        "run": run,
        "source_candidate": source.name,
        "query": QUERY,
        "started_utc": data["started_utc"],
        "overall_event_counts": dict(sorted(all_events.items())),
        "five_minute_bins": [
            {"seconds": [i * WINDOW_SECONDS, (i + 1) * WINDOW_SECONDS],
             "count": sum(bin_.values()), "event_counts": dict(sorted(bin_.items()))}
            for i, bin_ in enumerate(bins)
        ],
        "reference_count": reference["count"],
        "raw_logs_saved": False,
        "limitations": ["Only the mall-portal security stream; gateway events are not included."],
    }
    target = MEASUREMENTS / f"baseline-security-r{run}-{source.stem.split(f'baseline-candidate-r{run}-', 1)[1]}.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"file": str(target), "run": run, "events": sum(all_events.values()), "bins": [b["count"] for b in output["five_minute_bins"]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int, choices=range(1, 6), help="one run; default: all five")
    args = parser.parse_args()
    for run in ([args.run] if args.run else range(1, 6)):
        print(json.dumps(collect(run), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
