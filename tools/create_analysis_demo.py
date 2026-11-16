"""Create one clearly marked synthetic replay incident for the local worker demo."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request

from local_analysis_worker import HERE, lab_setting, oncall_url


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("rca", "security"), required=True)
    args = parser.parse_args()
    files = sorted((HERE / "measurements").glob("stages-red-*.json"))
    if not files:
        parser.error("measured red-stage JSON is required")
    source: Path = files[-1]
    run = json.loads(source.read_text(encoding="utf-8"))
    step = "error_order_without_price" if args.kind == "rca" else "security_foreign_order_denied"
    row = next(item for item in run["steps"] if item["step"] == step)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    event = {"kind": "error_500" if args.kind == "rca" else "security_signal",
             "fingerprint": f"demo.local.{args.kind}.{stamp}",
             "route": "/order/generateOrder" if args.kind == "rca" else "/order/detail/9001",
             "trace_id": row["trace_id"], "occurred_at": run["started_utc"],
             "source": "lab_replay"}
    token = lab_setting("LAB_ONCALL_INGEST_TOKEN")
    if not token:
        parser.error("run make setup-oncall first")
    request = urllib.request.Request(oncall_url() + "/api/events", method="POST",
        data=json.dumps(event).encode("utf-8"),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        receipt = json.load(response)
    print(json.dumps({"incident_id": receipt["incident_id"], "created": receipt["created"],
                      "kind": event["kind"], "source_file": source.name,
                      "replayed_not_live_alert": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
