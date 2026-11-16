#!/usr/bin/env python3
"""Copy one local course login field to the Mac clipboard without printing it."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


ALLOWED = {
    "LAB_ONCALL_VIEW_USER", "LAB_ONCALL_VIEW_PASSWORD",
    "LAB_NTFY_VIEW_USER", "LAB_NTFY_VIEW_PASSWORD",
}
ENV = Path(__file__).resolve().parents[1] / ".env"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("field", choices=sorted(ALLOWED))
    args = parser.parse_args()
    if not ENV.is_file():
        parser.error("lab/.env가 없습니다. 먼저 make setup-oncall을 실행하세요")
    fields = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            fields[key] = value
    value = fields.get(args.field)
    if not value:
        parser.error(f"{args.field} 값이 없습니다. make setup-oncall 상태를 확인하세요")
    subprocess.run(["pbcopy"], input=value, text=True, check=True)
    print(f"{args.field} 값을 Mac 클립보드에 복사했습니다. 화면과 문서에 붙여 넣지 마세요.")


if __name__ == "__main__":
    main()
