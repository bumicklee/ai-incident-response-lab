"""Static check of a Grafana alert provisioning file without PyYAML: rule blocks, required keys, unique uids.

    python3 tools/check_alert_rules.py <course-alerts.yaml>

Used by the login fix copy's run-tests.sh. It cannot prove that Grafana will load the file; the lab restarts
Grafana after deploy and the retest (make kit-login) is the real check.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

REQUIRED = ("title:", "condition:", "data:", "expr:", "labels:", "sre_kind:", "fingerprint:", "annotations:")


def main() -> int:
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    if "\t" in text:
        print("FAIL 탭 문자가 있습니다(YAML은 공백 들여쓰기)")
        return 1
    blocks = re.split(r"(?m)^(?=\s+- uid: )", text)[1:]
    uids = [re.match(r"\s+- uid: (\S+)", b).group(1) for b in blocks]
    problems = [f"uid 중복: {u}" for u, n in Counter(uids).items() if n > 1]
    for uid, block in zip(uids, blocks):
        if uid.endswith("webhook"):
            continue                       # contact points share the "- uid:" shape
        missing = [k for k in REQUIRED if k not in block]
        if missing:
            problems.append(f"{uid}: 빠진 칸 {missing}")
        expr = re.search(r"expr: '(.*)'", block)
        if expr and expr.group(1).count("(") != expr.group(1).count(")"):
            problems.append(f"{uid}: expr 괄호 짝이 맞지 않음")
    for p in problems:
        print("FAIL", p)
    rules = [u for u in uids if not u.endswith("webhook")]
    print(f"alerts: 규칙 {len(rules)}개 · {'OK' if not problems else '실패'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
