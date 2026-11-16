"""Require an on-call web approval before the lab fix agent can start.

This is one boundary in the supplied runner, not a general operating-system
sandbox. Do not give the model an independent file-writing tool in triage mode.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

from run_stages import HERE


def env_value(name: str, default: str = "") -> str:
    for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    return default


def approval(incident_id: int) -> dict:
    if incident_id < 1:
        raise ValueError("incident ID must be positive")
    port = env_value("LAB_ONCALL_PORT", "18090")
    url = f"http://127.0.0.1:{port}/api/approvals/{incident_id}"
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + env_value("LAB_ONCALL_ANALYSIS_TOKEN")})
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.load(response)
    if result.get("incident_id") != incident_id:
        raise ValueError("approval response is for a different incident")
    return result


def require(incident_id: int) -> dict:
    state = approval(incident_id)
    if not state.get("analysis_present") or not state.get("approved"):
        raise PermissionError("analysis and human fix approval are required")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incident-id", type=int, required=True)
    args = parser.parse_args()
    try:
        state = require(args.incident_id)
    except (PermissionError, urllib.error.HTTPError) as exc:
        print(json.dumps({"incident_id": args.incident_id, "fix_allowed": False,
                          "reason": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps({"incident_id": args.incident_id, "fix_allowed": True,
                      "approved_at": state["approved_at"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
