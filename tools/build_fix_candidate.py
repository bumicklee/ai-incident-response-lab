#!/usr/bin/env python3
"""Build the price-fix candidate from a patch in an isolated temporary copy."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


LAB = Path(__file__).resolve().parents[1]
PATCH = LAB / "course/reports/local-price-fix-candidate-20261114.patch"
IMAGE = "sre-lab/mall-portal:fix-candidate"
TEST = "spring/mall-portal/src/test/java/com/macro/mall/portal/LocalPriceFixTest.java"
SERVICE = "spring/mall-portal/src/main/java/com/macro/mall/portal/service/impl/OmsCartItemServiceImpl.java"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-only", action="store_true", help="Run four Java regression tests without building the app image")
    args = parser.parse_args()
    (LAB / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fix-candidate-", dir=LAB / "tmp") as temp:
        root = Path(temp)
        shutil.copytree(LAB / "spring", root / "spring", ignore=shutil.ignore_patterns("target", ".git", ".DS_Store"))
        # lab/ is itself a git repository: inside it, git apply silently skips every file ("Skipped patch").
        # Stop repository discovery at the temporary copy so the patch really applies, then confirm it did.
        env = {**os.environ, "GIT_CEILING_DIRECTORIES": str(root.parent)}
        subprocess.run(["git", "apply", "--check", str(PATCH)], cwd=root, env=env, check=True)
        subprocess.run(["git", "apply", str(PATCH)], cwd=root, env=env, check=True)
        if not (root / TEST).is_file() or 'SecureKitSwitch.on("server-price")' in (root / SERVICE).read_text(encoding="utf-8"):
            raise SystemExit("patch was not applied to the isolated copy; refusing to build")
        context = root / "spring"
        if args.unit_only:
            command = ["docker", "build", "--progress=plain", "-f", str(LAB / "course/reports/Dockerfile.local-price-fix-test"), str(context)]
        else:
            command = ["docker", "build", "--progress=plain", "-f", str(LAB / "infra/Dockerfile.portal"), "-t", IMAGE, str(context)]
        subprocess.run(command, cwd=LAB, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
