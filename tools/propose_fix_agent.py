"""Produce a reviewable 500 fix diff only after the on-call approval gate.

The model has no repository tools. This runner saves a proposed patch and does
not apply, commit, merge, or deploy it. A person reviews it before those steps.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from approval_gate import require
from run_rca_agent import HERE, packet


SOURCE = HERE / "spring/mall-portal/src/main/java/com/macro/mall/portal/service/impl/OmsPromotionServiceImpl.java"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    state = require(args.incident_id)
    source = SOURCE.read_text(encoding="utf-8")
    if len(source) > 30_000:
        raise SystemExit("source is larger than the reviewed input limit")
    evidence = packet()
    prompt = (
        "격리된 가짜 쇼핑몰의 승인된 500 사건에 대한 최소 수정 제안을 만드세요. "
        "당신에게 파일·셸 도구는 없습니다. 아래 Java 파일 하나에 대한 unified diff만 답하세요. "
        "가격 없는 장바구니가 주문으로 이어질 때 서버의 SKU 가격을 항상 적용하되, "
        "정상 주문과 할인 계산을 유지해야 합니다. 교육용 우회 스위치가 원인이라면 "
        "우회를 제거하는 방향을 검토하세요. 다른 파일·DB·보안 설정은 바꾸지 마세요. "
        "기존 줄을 확인할 수 없는 코드를 상상하지 마세요. 코드는 사람이 리뷰하고 "
        "레드 시험과 정상·할인 회귀 시험 뒤에만 적용합니다. "
        "출력은 diff --git으로 시작하는 패치 텍스트만 쓰세요.\n\n"
        f"승인 시각: {state['approved_at']}\n"
        f"재현 자료: {json.dumps({'red_steps': evidence['red_steps'], 'green_steps': evidence['green_steps']}, ensure_ascii=False)}\n"
        f"파일: {SOURCE.relative_to(HERE)}\n" + source
    )
    if args.dry_run:
        print(json.dumps({"kind": "dry_run_after_approval", "incident_id": args.incident_id,
                          "source_file": str(SOURCE.relative_to(HERE)), "prompt_chars": len(prompt)},
                         ensure_ascii=False))
        return 0
    result = subprocess.run(["claude", "-p", "--tools", "", "--permission-mode", "dontAsk",
                             "--no-session-persistence", "--max-budget-usd", "0.50", prompt],
                            cwd=HERE, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise SystemExit(f"fix proposal failed with exit code {result.returncode}; no patch saved")
    patch = result.stdout.strip()
    path_marker = str(SOURCE.relative_to(HERE))
    if not patch.startswith("diff --git ") or patch.count("diff --git ") != 1 or path_marker not in patch:
        raise SystemExit("model output is not a single-file unified diff; no patch saved")
    if len(patch) > 20_000 or "+++ /dev/null" in patch or "--- /dev/null" in patch:
        raise SystemExit("proposal is outside the reviewed patch bounds")
    target = HERE / "measurements" / f"agent-fix-proposal-500-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.patch"
    target.write_text(patch + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "kind": "proposal_only_not_applied",
                      "incident_id": args.incident_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
