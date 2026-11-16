"""Give bounded, redacted lab evidence to Claude for a read-only 500 RCA.

The model receives a packet rather than repository tool access. The report is
saved separately from the teacher's answer fixture. No code edits are allowed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

from run_stages import HERE


MEASUREMENTS = HERE / "measurements"
SOURCE = HERE / "spring/mall-portal/src/main/java/com/macro/mall/portal"
SNIPPETS = (
    (SOURCE / "controller/OmsCartItemController.java", 43, 54),
    (SOURCE / "controller/OmsCartItemController.java", 174, 190),
    (SOURCE / "service/impl/OmsCartItemServiceImpl.java", 75, 94),
    (SOURCE / "service/impl/OmsPromotionServiceImpl.java", 180, 194),
    (SOURCE / "service/impl/OmsPortalOrderServiceImpl.java", 894, 909),
)
SCHEMA = {
    "type": "object",
    "properties": {
        "analysis_kind": {"type": "string", "enum": ["rca_500"]},
        "author": {"type": "string", "enum": ["agent"]},
        "observations": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 10},
        "hypotheses": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
        "unknowns": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 8},
        "evidence_refs": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 12},
        "next_action": {"type": "string"},
    },
    "required": ["analysis_kind", "author", "observations", "hypotheses",
                 "unknowns", "evidence_refs", "next_action"],
    "additionalProperties": False,
}


def snippet(path: Path, first: int, last: int) -> dict:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {"path": str(path.relative_to(HERE)), "line_range": [first, last],
            "source": "\n".join(f"{number}: {lines[number - 1]}"
                                 for number in range(first, min(last, len(lines)) + 1))}


def packet() -> dict:
    red_name = "stages-red-20261114T003106Z.json"
    green_name = "stages-green-20261114T003602Z.json"
    red = json.loads((MEASUREMENTS / red_name).read_text(encoding="utf-8"))
    green = json.loads((MEASUREMENTS / green_name).read_text(encoding="utf-8"))
    logs = json.loads((MEASUREMENTS / "log-evidence-20261114.json").read_text(encoding="utf-8"))
    wanted = {"normal_home", "normal_own_order", "error_cart_add_without_price",
              "error_order_without_price"}
    return {
        "scope": "isolated synthetic backend incident lab, not a production incident",
        "red_switches": {"server_price_enabled": False, "return_owner_enabled": False},
        "green_switches": {"server_price_enabled": True, "return_owner_enabled": True},
        "red_file": red_name,
        "green_file": green_name,
        "red_steps": [s for s in red["steps"] if s["step"] in wanted],
        "green_steps": [s for s in green["steps"] if s["step"] in wanted],
        "log_file": "log-evidence-20261114.json",
        "log_records": [r for r in logs["records"] if r["step"] == "error_order_without_price"],
        "source_excerpts": [snippet(*spec) for spec in SNIPPETS],
        "note": "The Loki summary has no complete Java stack frames; do not assert an exact throw line from it.",
    }


def prompt_for(evidence: dict) -> str:
    return ("당신은 백엔드 장애·해킹 탐지 강의의 읽기 전용 원인 분석가입니다. 아래는 격리된 가짜 쇼핑몰의 실측 자료와 "
            "관련 Java 코드 일부입니다. 저장소와 네트워크에 접근하지 말고, 제공한 자료만으로 답하세요. "
            "HTTP 상태와 본문 code를 구분하고, 관측 사실·원인 가설·미확인을 나누세요. "
            "가격 누락, 로그인, DB 오류 가설을 비교하세요. 전체 사용자 영향, 운영 장애 시간, "
            "정확한 Java 예외 행은 근거 없이 단정하지 마세요. 그린은 랩 스위치 전환이지 PR 배포가 아닙니다. "
            "수정 방향은 서버 SKU 가격을 기준으로 하되 사람이 승인하기 전에는 코드 변경을 제안만 하세요. "
            "비밀번호·토큰·전화번호·주소·전체 요청 본문은 출력하지 마세요. "
            "각 근거는 제공한 파일 경로·단계 이름·trace_id 또는 코드 위치로 적으세요. "
            "지정된 JSON 스키마만 출력하세요.\n\n실측 자료:\n" +
            json.dumps(evidence, ensure_ascii=False, indent=2))


def validate(report: dict) -> None:
    if set(report) != set(SCHEMA["required"]) or report["analysis_kind"] != "rca_500" or report["author"] != "agent":
        raise ValueError("AI report does not match the required field set")
    for key in ("observations", "hypotheses", "unknowns", "evidence_refs"):
        value = report[key]
        if not isinstance(value, list) or not 1 <= len(value) <= 20 or any(
                not isinstance(line, str) or not 1 <= len(line.strip()) <= 500 for line in value):
            raise ValueError(f"invalid {key} field")
    if not isinstance(report["next_action"], str) or not 1 <= len(report["next_action"].strip()) <= 500:
        raise ValueError("invalid next_action field")
    all_text = json.dumps(report, ensure_ascii=False)
    if re.search(r"(?i)bearer\s+[a-z0-9._-]{12,}|daon-pass-1!|01[016789]-?\d{3,4}-?\d{4}", all_text):
        raise ValueError("report appears to contain a secret or phone number")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Check packet and schema without calling Claude")
    args = parser.parse_args()
    evidence = packet()
    prompt = prompt_for(evidence)
    if args.dry_run:
        print(json.dumps({"kind": "dry_run_no_model_call", "red_steps": len(evidence["red_steps"]),
                          "green_steps": len(evidence["green_steps"]),
                          "log_records": len(evidence["log_records"]),
                          "code_excerpts": len(evidence["source_excerpts"]),
                          "prompt_chars": len(prompt)}, ensure_ascii=False))
        return 0

    command = ["claude", "-p", "--tools", "", "--permission-mode", "dontAsk",
               "--no-session-persistence", "--max-budget-usd", "0.50",
               "--output-format", "json", "--json-schema", json.dumps(SCHEMA), prompt]
    # 구독 로그인으로만 부른다: 셸에 API 키가 있어도 실수로 API 요금이 나가지 않게 지운다(local_analysis_worker와 같음)
    cli_env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    result = subprocess.run(command, cwd=HERE, env=cli_env, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise SystemExit(f"Claude RCA failed with exit code {result.returncode}; no report was saved")
    envelope = json.loads(result.stdout)
    if isinstance(envelope, list):
        envelope = next((item for item in reversed(envelope)
                         if isinstance(item, dict) and item.get("type") == "result"), None)
    if not isinstance(envelope, dict):
        raise SystemExit("Claude RCA returned no result envelope; no report was saved")
    report = envelope.get("structured_output")
    if report is None:
        report = json.loads(envelope["result"])
    validate(report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = MEASUREMENTS / f"agent-rca-500-{stamp}.json"
    output = {"kind": "ai_rca_report", "created_utc": datetime.now(timezone.utc).isoformat(),
              "model_report": report, "packet_source_files": [evidence["red_file"], evidence["green_file"],
                                                    evidence["log_file"]],
              "usage": envelope.get("usage"), "total_cost_usd": envelope.get("total_cost_usd"),
              "read_only_tools_disabled": True}
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "kind": output["kind"],
                      "observations": len(report["observations"]),
                      "evidence_refs": len(report["evidence_refs"]),
                      "cost_usd": output["total_cost_usd"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
