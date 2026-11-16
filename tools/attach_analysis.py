"""Attach a validated local AI RCA or security triage to one on-call incident."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.request

from run_stages import HERE
from run_rca_agent import validate as validate_rca
from run_security_agent import validate as validate_security


def local_token() -> str:
    values = {}
    for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    token = values.get("LAB_ONCALL_ANALYSIS_TOKEN", "")
    if not token:
        raise ValueError("LAB_ONCALL_ANALYSIS_TOKEN is missing; run setup-oncall migration")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident-id", type=int, required=True)
    parser.add_argument("--kind", choices=("rca", "security"), default="rca")
    parser.add_argument("--report", type=Path, help="agent JSON inside measurements; default: latest of --kind")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.incident_id < 1:
        parser.error("incident-id must be positive")
    pattern = "agent-rca-500-*.json" if args.kind == "rca" else "agent-security-triage-*.json"
    reports = sorted((HERE / "measurements").glob(pattern))
    path = args.report or (reports[-1] if reports else None)
    if path is None or path.resolve().parent != (HERE / "measurements").resolve():
        parser.error("report must be a file directly inside measurements/")
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = "ai_rca_report" if args.kind == "rca" else "ai_security_triage_report"
    if data.get("kind") != expected:
        raise ValueError(f"report kind is not {expected}")
    report = data["model_report"]
    (validate_rca if args.kind == "rca" else validate_security)(report)
    if args.dry_run:
        print(json.dumps({"kind": "dry_run_no_post", "report": path.name,
                          "incident_id": args.incident_id, "evidence_refs": len(report["evidence_refs"])},
                         ensure_ascii=False))
        return 0
    port = "18090"
    for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LAB_ONCALL_PORT="):
            port = line.split("=", 1)[1]
    url = f"http://127.0.0.1:{port}/api/incidents/{args.incident_id}/analysis"
    request = urllib.request.Request(url, method="POST",
                                     data=json.dumps(report, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + local_token(),
                                              "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    print(json.dumps({"incident_id": result["incident_id"], "analysis_kind": result["analysis_kind"],
                      "export_url": result["export_url"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
