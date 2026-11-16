"""Replay measured red-stage facts into the on-call inbox, clearly marked as replay.

This does not claim Grafana Alerting fired. Use it to teach the web UI before
alert rules and baseline thresholds have been measured and provisioned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import urllib.request

from run_stages import HERE


def read_token() -> str:
    env = HERE / ".env"
    if not env.exists():
        raise SystemExit("make setup-oncall을 먼저 실행하세요")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("LAB_ONCALL_INGEST_TOKEN="):
            return line.split("=", 1)[1]
    raise SystemExit(".env에 LAB_ONCALL_INGEST_TOKEN이 없습니다")


def post(url: str, token: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url + "/api/events", body,
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + token},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


def main() -> None:
    red = sorted((HERE / "measurements").glob("stages-red-*.json"))
    if not red:
        raise SystemExit("레드 단건 실측 JSON이 없습니다")
    source = red[-1]
    data = json.loads(source.read_text(encoding="utf-8"))
    steps = {row["step"]: row for row in data["steps"]}
    error = steps["error_order_without_price"]
    denied = steps["security_foreign_order_denied"]
    if error["http_status"] != 500 or denied["body_code"] != 500:
        raise SystemExit("원본 레드 측정값이 기대와 달라 재생하지 않습니다")
    now = datetime.now(timezone.utc)
    events = [
        {"kind": "error_500", "fingerprint": "NullPointerException:order.generateOrder",
         "route": "/order/generateOrder", "trace_id": error["trace_id"],
         "occurred_at": now.isoformat(), "source": "lab_replay"},
        {"kind": "security_signal", "fingerprint": "access.denied.not_owner:order.detail",
         "route": "/order/detail/9001", "trace_id": denied["trace_id"],
         "occurred_at": (now + timedelta(seconds=1)).isoformat(), "source": "lab_replay"},
    ]
    url = "http://127.0.0.1:" + os.environ.get("LAB_ONCALL_PORT", "18090")
    token = read_token()
    receipts = [post(url, token, event) for event in events]
    output = HERE / "measurements" / f"incident-replay-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
    output.write_text(json.dumps({"kind": "lab_replay_not_grafana_alert",
                                  "source_file": source.name, "events": events,
                                  "receipts": receipts}, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({"file": str(output), "incident_ids": [r["incident_id"] for r in receipts],
                      "kind": "lab_replay_not_grafana_alert"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
