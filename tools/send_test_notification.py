"""Publish one harmless local ntfy message to test Android delivery.

This is a transport probe, not a Grafana alert or a production incident.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request


HERE = Path(__file__).resolve().parents[1]


def main() -> None:
    values = dict(line.split("=", 1) for line in (HERE / ".env").read_text(encoding="utf-8").splitlines()
                  if "=" in line and not line.startswith("#"))
    payload = {"topic": "sre-lab", "title": "연결 시험", "message": "Android 배경 수신 확인 · 실제 사건 아님"}
    request = urllib.request.Request("http://127.0.0.1:18091/",
                                     data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + values["LAB_NTFY_PUBLISH_TOKEN"]},
                                     method="POST")
    with urllib.request.urlopen(request, timeout=8) as response:
        result = json.load(response)
    print(json.dumps({"kind": "local_ntfy_transport_probe_not_incident",
                      "sent_utc": datetime.now(timezone.utc).isoformat(),
                      "status": 200, "message_id": result.get("id"),
                      "title": payload["title"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
