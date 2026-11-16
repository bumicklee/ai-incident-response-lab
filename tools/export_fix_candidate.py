"""Export a reviewable patch from a separate Spring work copy without changing the lab."""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path

from run_stages import HERE


FILES = (
    "mall-portal/src/main/java/com/macro/mall/portal/controller/OmsCartItemController.java",
    "mall-portal/src/main/java/com/macro/mall/portal/service/impl/OmsCartItemServiceImpl.java",
    "mall-portal/src/main/java/com/macro/mall/portal/service/impl/OmsPromotionServiceImpl.java",
    "mall-portal/src/main/java/com/macro/mall/portal/service/impl/OmsPortalOrderServiceImpl.java",
    "mall-portal/src/test/java/com/macro/mall/portal/LocalPriceFixTest.java",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, required=True)
    args = parser.parse_args()
    chunks = []
    for relative in FILES:
        source = HERE / "spring" / relative
        candidate = args.candidate_root / relative
        if not candidate.is_file():
            parser.error(f"candidate file missing: {relative}")
        before = source.read_text(encoding="utf-8").splitlines(keepends=True) if source.exists() else []
        after = candidate.read_text(encoding="utf-8").splitlines(keepends=True)
        if before == after:
            parser.error(f"candidate has no change: {relative}")
        name = "spring/" + relative
        chunks.append(f"diff --git a/{name} b/{name}\n")
        if not source.exists():
            chunks.append("new file mode 100644\n")
        chunks.extend(difflib.unified_diff(before, after,
            fromfile="a/" + name if source.exists() else "/dev/null",
            tofile="b/" + name, n=3))
    target = HERE / "course" / "reports" / "local-price-fix-candidate-20261114.patch"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(chunks), encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
