"""Capture a single service/memory snapshot; this is not a peak measurement."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "measurements"


def call(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or f"명령 실패: {' '.join(args)}")
    return result.stdout.strip()


def main() -> None:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    rows = [json.loads(line) for line in call("docker", "compose", "-f", "compose.yaml", "ps", "--format", "json").splitlines() if line]
    ids = [row["ID"] for row in rows if row.get("State") == "running"]
    stats = [json.loads(line) for line in call("docker", "stats", "--no-stream", "--format", "{{json .}}", *ids).splitlines() if line] if ids else []
    data = {
        "recorded_at": now.isoformat(),
        "kind": "single_snapshot_not_peak",
        "environment_record": max(OUT.glob("environment-*.json"), default=Path("missing")).name,
        "services": [{"name": row.get("Name"), "service": row.get("Service"), "state": row.get("State"), "health": row.get("Health"), "publishers": row.get("Publishers")} for row in rows],
        "docker_stats": stats,
    }
    OUT.mkdir(exist_ok=True)
    path = OUT / f"services-{now.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(rows)}개 서비스의 순간 상태: {path}")


if __name__ == "__main__":
    main()
