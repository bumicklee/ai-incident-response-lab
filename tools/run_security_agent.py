"""Ask Claude for bounded security triage without giving it the defect answer.

Only synthetic status/count evidence is sent. The red return result is withheld
so the agent can recommend a separate human-controlled confirmation test.
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
SCHEMA = {
    "type": "object",
    "properties": {
        "analysis_kind": {"type": "string", "enum": ["security_triage"]},
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


def latest(scenario: str) -> tuple[str, dict]:
    paths = sorted(MEASUREMENTS.glob(f"security-{scenario}-*.json"))
    if not paths:
        raise FileNotFoundError(f"no measured {scenario} scenario")
    return paths[-1].name, json.loads(paths[-1].read_text(encoding="utf-8"))


def packet() -> dict:
    cases = {}
    for scenario in ("normal", "spray", "idor"):
        name, data = latest(scenario)
        cases[scenario] = {
            "source_file": name,
            "requests": [{key: row[key] for key in ("step", "http_status", "body_code", "trace_id", "target")}
                         for row in data["requests"]],
            "loki_event_counts": data["loki_event_counts"],
        }
    return {"scope": "isolated synthetic lab; no real account or production attack",
            "cases": cases,
            "known_behavior": "A denied order-detail request does not prove that other order APIs check ownership.",
            "withheld": "Return-application red result and code are intentionally absent. Propose a test, do not claim its outcome."}


def validate(report: dict) -> None:
    if set(report) != set(SCHEMA["required"]) or report["analysis_kind"] != "security_triage" or report["author"] != "agent":
        raise ValueError("security report has the wrong fields")
    for field in ("observations", "hypotheses", "unknowns", "evidence_refs"):
        lines = report[field]
        if not isinstance(lines, list) or not 1 <= len(lines) <= 20 or any(
                not isinstance(line, str) or not 1 <= len(line.strip()) <= 500 for line in lines):
            raise ValueError(f"invalid {field}")
    if not isinstance(report["next_action"], str) or not 1 <= len(report["next_action"].strip()) <= 500:
        raise ValueError("invalid next_action")
    all_text = json.dumps(report, ensure_ascii=False)
    if re.search(r"(?i)bearer\s+[a-z0-9._-]{12,}|daon-pass-1!|01[016789]-?\d{3,4}-?\d{4}", all_text):
        raise ValueError("report may expose a credential or phone number")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    evidence = packet()
    prompt = ("당신은 합성 랩의 읽기 전용 보안 조사자입니다. 주어진 상태·이벤트 건수만으로 "
              "관측 사실, 공격/정상 반례를 포함한 가설, 아직 모르는 점을 분리하세요. "
              "HTTP 200/본문 500은 거부이며 HTTP 500과 다릅니다. 401·429 단건은 공격 확정이 아닙니다. "
              "타인 주문 조회 거부만으로 반품 API 결함이나 실제 침해를 단정하지 마세요. "
              "반품 API에 대한 사람의 가짜 계정 확인 시험을 다음 행동으로 제안하고, 시험 결과는 쓰지 마세요. "
              "비밀번호·토큰·개인정보·전체 요청 본문은 출력하지 마세요. "
              "각 근거는 제공된 파일 이름·step·trace_id로 적으세요. 지정된 JSON 스키마만 답하세요.\n\n" +
              json.dumps(evidence, ensure_ascii=False, indent=2))
    if args.dry_run:
        print(json.dumps({"kind": "dry_run_no_model_call", "cases": list(evidence["cases"]),
                          "prompt_chars": len(prompt)}, ensure_ascii=False))
        return 0
    command = ["claude", "-p", "--tools", "", "--permission-mode", "dontAsk",
               "--no-session-persistence", "--max-budget-usd", "0.50",
               "--output-format", "json", "--json-schema", json.dumps(SCHEMA), prompt]
    # 구독 로그인으로만 부른다: 셸에 API 키가 있어도 실수로 API 요금이 나가지 않게 지운다(local_analysis_worker와 같음)
    cli_env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    result = subprocess.run(command, cwd=HERE, env=cli_env, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise SystemExit(f"Claude security triage failed with exit code {result.returncode}; no report saved")
    envelope = json.loads(result.stdout)
    if isinstance(envelope, list):
        envelope = next((item for item in reversed(envelope)
                         if isinstance(item, dict) and item.get("type") == "result"), None)
    if not isinstance(envelope, dict):
        raise SystemExit("Claude security triage returned no result envelope; no report was saved")
    report = envelope.get("structured_output")
    if report is None:
        report = json.loads(envelope["result"])
    validate(report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = MEASUREMENTS / f"agent-security-triage-{stamp}.json"
    output = {"kind": "ai_security_triage_report", "created_utc": datetime.now(timezone.utc).isoformat(),
              "model_report": report,
              "packet_source_files": [case["source_file"] for case in evidence["cases"].values()],
              "read_only_tools_disabled": True,
              "return_defect_result_withheld": True,
              "usage": envelope.get("usage"), "total_cost_usd": envelope.get("total_cost_usd")}
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "observations": len(report["observations"]),
                      "evidence_refs": len(report["evidence_refs"]),
                      "cost_usd": output["total_cost_usd"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
