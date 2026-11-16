"""Read-only checks before measuring the lab."""

import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTS = {
    "portal": int(os.environ.get("LAB_PORTAL_PORT", "18085")),
    "Grafana": int(os.environ.get("LAB_GRAFANA_PORT", "13030")),
    "Loki": int(os.environ.get("LAB_LOKI_PORT", "13100")),
}
FILES = (
    "compose.yaml",
    "spring/pom.xml",
    "spring/mall-portal/pom.xml",
    "spring/document/sql/mall.sql",
    "seed/spring/20-daon-accounts.sql",
    "infra/observability/alloy/config.alloy",
)


def command(*args: str) -> tuple[bool, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=20, cwd=ROOT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return done.returncode == 0, (done.stdout or done.stderr).strip()


def main() -> int:
    failed = []
    print(f"OS: {platform.platform()}")
    if sys.platform != "darwin":
        failed.append("이 강의의 실측 대상은 macOS입니다")
    for filename in FILES:
        if not (ROOT / filename).is_file():
            failed.append(f"파일 없음: {filename}")
    for executable in ("docker", "git", "make", "python3"):
        if not shutil.which(executable):
            failed.append(f"명령 없음: {executable}")
    if shutil.which("docker"):
        ok, message = command("docker", "info", "--format", "{{.ServerVersion}}")
        print(f"Docker engine: {message if ok else '연결 실패'}")
        if not ok:
            failed.append("Docker 엔진 연결 실패")
        ok, message = command("docker", "compose", "-f", "compose.yaml", "config", "--quiet")
        if not ok:
            failed.append(f"Compose 설정 오류: {message}")
    for service, port in PORTS.items():
        with socket.socket() as sock:
            sock.settimeout(0.3)
            busy = sock.connect_ex(("127.0.0.1", port)) == 0
        print(f"{service} port {port}: {'사용 중' if busy else '비어 있음'}")
        if busy:
            failed.append(f"포트 사용 중: {port} (해당 LAB_*_PORT 환경 변수로 변경 가능)")
    if failed:
        print("\n준비 확인 실패:")
        for item in failed:
            print(f"- {item}")
        return 1
    print("\n정적 준비 확인 통과. 실제 서비스 기동·사건 재현은 별도 검증이 필요합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
