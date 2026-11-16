"""Save small, privacy-safe Loki evidence for the measured stage traces."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re

from run_stages import HERE, loki_entries

NAMES = ["security_foreign_order_denied", "error_order_without_price", "security_foreign_return"]


def main():
    parser = argparse.ArgumentParser(description="단계 JSON의 trace ID로 Loki 증거 수집")
    parser.add_argument("--red", type=Path, help="레드 단계 JSON. 생략하면 가장 최근 파일")
    parser.add_argument("--green", type=Path, help="그린 단계 JSON. 생략하면 가장 최근 파일")
    args = parser.parse_args()
    red_files = sorted((HERE / "measurements").glob("stages-red-*.json"))
    green_files = sorted((HERE / "measurements").glob("stages-green-*.json"))
    files = [args.red or (red_files[-1] if red_files else None),
             args.green or (green_files[-1] if green_files else None)]
    if any(path is None for path in files):
        parser.error("레드와 그린 단계 JSON이 모두 필요합니다")
    records = []
    for path in files:
        run = json.loads(path.read_text())
        start = int(dt.datetime.fromisoformat(run["started_utc"]).timestamp() * 1e9)
        end = int(dt.datetime.fromisoformat(run["ended_utc"]).timestamp() * 1e9) + 10_000_000_000
        for name in NAMES:
            step = next(x for x in run["steps"] if x["step"] == name)
            trace = step["trace_id"]
            rows = loki_entries('{app="mall-portal"} |= "' + trace + '"', start, end)
            events = []
            for row in rows:
                match = re.search(r"\{.*\}\s*$", row["line"])
                if not match:
                    continue
                try:
                    payload = json.loads(match.group())
                except ValueError:
                    continue
                if "event" in payload:
                    events.append({k: payload[k] for k in ("event", "lesson", "reason", "target", "trace_id") if k in payload})
            records.append({"mode": run["mode"], "step": name, "trace_id": trace,
                            "loki_lines_with_trace": len(rows), "events": events,
                            "null_pointer_exception_lines": sum("NullPointerException" in x["line"] for x in rows),
                            "sql_error_lines": sum("데이터베이스 예외" in x["line"] for x in rows)})
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = HERE / "measurements" / f"log-evidence-{stamp}.json"
    out.write_text(json.dumps({"source": "Loki query_range by each stage trace ID",
                               "stage_files": [x.name for x in files], "records": records}, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"file": str(out), "records": records}, ensure_ascii=False))


if __name__ == "__main__":
    main()
