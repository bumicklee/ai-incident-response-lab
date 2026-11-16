"""Read-only live checks for the copied app and observation stack."""

import json
import os
import sys
import http.client
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


def get(url: str) -> tuple[int | None, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected, ConnectionResetError):
        return None, b""


def main() -> int:
    portal = os.environ.get("LAB_PORTAL_PORT", "18085")
    grafana = os.environ.get("LAB_GRAFANA_PORT", "13030")
    loki = os.environ.get("LAB_LOKI_PORT", "13100")
    checks = {
        "Spring home": f"http://127.0.0.1:{portal}/home/content",
        "Grafana health": f"http://127.0.0.1:{grafana}/api/health",
        "Loki ready": f"http://127.0.0.1:{loki}/ready",
    }
    failed = []
    for label, url in checks.items():
        status, body = get(url)
        print(f"{label}: HTTP {status or 'unreachable'}")
        if status != 200:
            failed.append(label)
    now = datetime.now(timezone.utc)
    query = urllib.parse.urlencode({
        "query": '{app="mall-portal"}',
        "start": str(int((now - timedelta(hours=1)).timestamp() * 1_000_000_000)),
        "end": str(int(now.timestamp() * 1_000_000_000)),
        "limit": "1",
        "direction": "backward",
    })
    status, body = get(f"http://127.0.0.1:{loki}/loki/api/v1/query_range?{query}")
    streams = []
    if status == 200:
        try:
            streams = json.loads(body)["data"]["result"]
        except (KeyError, ValueError):
            pass
    print(f"Loki mall-portal stream: {'found' if streams else 'missing'}")
    if not streams:
        failed.append("Alloy→Loki app log")
    if failed:
        print("미통과: " + ", ".join(failed))
        return 1
    print("기동·로그 수집 확인. 이 스모크 시험만으로 30분 기준선은 검증되지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
