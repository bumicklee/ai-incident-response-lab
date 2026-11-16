"""Create local-only on-call/ntfy credentials without printing secret values."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import string
import subprocess


HERE = Path(__file__).resolve().parents[1]
ENV = HERE / ".env"
CONFIG = HERE / "runtime/ntfy/server.yml"
ALPHABET = string.ascii_lowercase + string.digits


def random_value(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def password_hash(user: str, password: str) -> str:
    result = subprocess.run(["htpasswd", "-C", "12", "-nbB", user, password], check=True,
                            capture_output=True, text=True)
    return result.stdout.strip().split(":", 1)[1]


def write_ntfy_config(values: dict[str, str]) -> None:
    """Write ntfy's supported bcrypt cost while keeping existing credentials."""
    yaml_lines = [
        'base-url: "http://127.0.0.1:18091"',
        'listen-http: ":80"',
        'cache-file: "/var/cache/ntfy/cache.db"',
        'auth-file: "/var/cache/ntfy/user.db"',
        'auth-default-access: "deny-all"',
        'auth-users:',
        '  - ' + json.dumps("publisher:" + password_hash("publisher", values["LAB_NTFY_PUBLISH_PASSWORD"]) + ":user"),
        '  - ' + json.dumps("viewer:" + password_hash("viewer", values["LAB_NTFY_VIEW_PASSWORD"]) + ":user"),
        'auth-access:',
        '  - "publisher:sre-lab:wo"',
        '  - "viewer:sre-lab:ro"',
        'auth-tokens:',
        '  - ' + json.dumps("publisher:" + values["LAB_NTFY_PUBLISH_TOKEN"] + ":course-ingest"),
        '',
    ]
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text("\n".join(yaml_lines), encoding="utf-8")
    CONFIG.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--add-analysis-token", action="store_true",
                        help="Add only the new analysis and CSRF secrets to an existing .env")
    parser.add_argument("--repair-auth-hashes", action="store_true",
                        help="Regenerate only ntfy config with stronger bcrypt hashes; keep .env values")
    args = parser.parse_args()
    if args.repair_auth_hashes:
        if not ENV.exists() or not CONFIG.exists():
            raise SystemExit("기존 .env와 ntfy 설정이 모두 필요합니다.")
        values = dict(line.split("=", 1) for line in ENV.read_text(encoding="utf-8").splitlines()
                      if "=" in line and not line.startswith("#"))
        required = {"LAB_NTFY_PUBLISH_PASSWORD", "LAB_NTFY_VIEW_PASSWORD", "LAB_NTFY_PUBLISH_TOKEN"}
        if not required <= values.keys():
            raise SystemExit("ntfy 인증값이 누락됐습니다. 기존 설정을 변경하지 않았습니다.")
        write_ntfy_config(values)
        print("기존 인증값을 유지하고 ntfy bcrypt 해시만 지원되는 강도로 다시 만들었습니다.")
        return
    if args.add_analysis_token:
        if not ENV.exists():
            raise SystemExit(".env가 없습니다. 먼저 옵션 없이 초기 설정을 생성하세요.")
        existing = ENV.read_text(encoding="utf-8")
        additions = {
            "LAB_ONCALL_ANALYSIS_TOKEN": random_value(40),
            "LAB_ONCALL_CSRF_SECRET": random_value(48),
        }
        missing = {key: value for key, value in additions.items()
                   if not any(line.startswith(key + "=") for line in existing.splitlines())}
        if not missing:
            print("분석·CSRF 비밀값이 이미 있습니다. 값을 바꾸지 않았습니다.")
            return
        with ENV.open("a", encoding="utf-8") as stream:
            for key, value in missing.items():
                stream.write(key + "=" + value + "\n")
        ENV.chmod(0o600)
        print("기존 .env에 누락된 분석·CSRF 비밀값을 추가했습니다. 값은 출력하지 않습니다.")
        return
    if ENV.exists() or CONFIG.exists():
        raise SystemExit("기존 .env 또는 ntfy 설정을 덮어쓰지 않습니다. 현재 파일을 먼저 확인하세요.")
    values = {
        "LAB_ONCALL_INGEST_TOKEN": random_value(40),
        "LAB_ONCALL_ANALYSIS_TOKEN": random_value(40),
        "LAB_ONCALL_CSRF_SECRET": random_value(48),
        "LAB_ONCALL_VIEW_USER": "viewer",
        "LAB_ONCALL_VIEW_PASSWORD": random_value(24),
        "LAB_NTFY_PUBLISH_TOKEN": "tk_" + random_value(29),
        "LAB_NTFY_VIEW_USER": "viewer",
        "LAB_NTFY_VIEW_PASSWORD": random_value(24),
        "LAB_NTFY_PUBLISH_PASSWORD": random_value(24),
    }
    write_ntfy_config(values)
    ENV.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n", encoding="utf-8")
    ENV.chmod(0o600)
    print("강의용 .env와 runtime/ntfy/server.yml을 생성했습니다. 두 파일은 Git에서 제외됩니다.")


if __name__ == "__main__":
    main()
