"""Analyze queued synthetic on-call incidents on this PC with CLI or Messages API.

One job is claimed at a time. The model has no repository, network, shell, or
deployment tools. Only a small incident packet and bounded code excerpts go to
the model; results are validated before attaching them to the on-call inbox.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

from run_stages import HERE, loki_entries
from run_rca_agent import SCHEMA as RCA_SCHEMA, SNIPPETS, snippet, validate as validate_rca
from run_security_agent import SCHEMA as SECURITY_SCHEMA, validate as validate_security


TRACE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
MODEL = os.environ.get("LAB_API_MODEL", "claude-sonnet-4-6")
API_VERSION = "2026-10-21"


def lab_setting(name: str, default: str = "") -> str:
    path = HERE / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return default


def oncall_url() -> str:
    port = lab_setting("LAB_ONCALL_PORT", "18090")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("LAB_ONCALL_PORT must be a local TCP port")
    return f"http://127.0.0.1:{port}"


def oncall(method: str, path: str, payload: dict) -> dict:
    token = lab_setting("LAB_ONCALL_ANALYSIS_TOKEN")
    if not token:
        raise RuntimeError("run make setup-oncall before the local worker")
    request = urllib.request.Request(oncall_url() + path, method=method,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def log_evidence(events: list[dict]) -> list[dict]:
    """Return counts and event names, never raw Loki lines or personal data."""
    evidence = []
    seen = set()
    for event in events:
        trace = str(event.get("trace_id") or "")
        if not TRACE_RE.fullmatch(trace) or trace in seen:
            continue
        seen.add(trace)
        if len(seen) > 3:
            break
        when = datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
        if when.tzinfo is None:
            continue
        start = int((when - timedelta(minutes=2)).timestamp() * 1e9)
        end = int((when + timedelta(minutes=2)).timestamp() * 1e9)
        try:
            rows = loki_entries('{app="mall-portal"} |= "' + trace + '"', start, end)[:100]
            names = []
            for row in rows:
                match = re.search(r"\{.*\}\s*$", row["line"])
                if match:
                    try:
                        name = json.loads(match.group()).get("event")
                    except ValueError:
                        name = None
                    if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
                        names.append(name)
            evidence.append({"trace_id": trace, "loki_lines": len(rows),
                             "event_names": sorted(set(names)),
                             "null_pointer_lines": sum("NullPointerException" in row["line"] for row in rows),
                             "database_error_lines": sum("데이터베이스 예외" in row["line"] for row in rows)})
        except (urllib.error.URLError, TimeoutError, ValueError):
            evidence.append({"trace_id": trace, "loki_query": "unavailable"})
    return evidence


EVENT_RE = re.compile(r"^[a-z0-9_]+(?:\.[a-z0-9_]+){1,3}$")


def event_statistics(incident: dict) -> dict:
    """Security incidents: count the fingerprint's events (and its family) in Loki around the alert.

    The alert itself carries no trace ID, so without this the model sees one Grafana row and nothing else
    (2026-11-15, incident #8). Only counts and IDs leave this function, never raw lines.
    """
    fingerprint = str(incident.get("fingerprint") or "")
    if not EVENT_RE.fullmatch(fingerprint):
        return {"status": "no_event_fingerprint"}
    family = ".".join(fingerprint.split(".")[:2])          # auth.login.rate_limited -> auth.login
    first = datetime.fromisoformat(str(incident["first_seen"]).replace("Z", "+00:00"))
    last = datetime.fromisoformat(str(incident["last_seen"]).replace("Z", "+00:00"))
    start, end = first - timedelta(minutes=10), last + timedelta(minutes=2)
    query = '{app="mall-portal",stream="security"} |= "\\"event\\":\\"' + family + '."'
    try:
        rows = loki_entries(query, int(start.timestamp() * 1e9), int(end.timestamp() * 1e9))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return {"status": "loki_unavailable", "window_utc": [start.isoformat(), end.isoformat()]}
    stats = {"by_event": {}, "by_source": {}, "by_target_kind": {}, "top_targets": {}, "by_reason": {}, "per_minute": {}}
    traces = []
    for row in rows:
        try:
            event = json.loads(row["line"])
        except ValueError:
            continue
        name = str(event.get("event", ""))
        if not EVENT_RE.fullmatch(name):
            continue
        source = "secure-kit:" + str(event["part"]) if event.get("part") else "app"
        target = str(event.get("target") or "-")[:60]
        minute = datetime.fromtimestamp(int(row["timestamp_ns"]) / 1e9, timezone.utc).strftime("%H:%M")
        for key, value in (("by_event", name), ("by_source", source), ("by_target_kind", target.split(":")[0]),
                           ("by_reason", str(event.get("reason") or "")[:80]), ("per_minute", minute)):
            stats[key][value] = stats[key].get(value, 0) + 1
        if name == fingerprint:
            stats["top_targets"][target] = stats["top_targets"].get(target, 0) + 1
            trace = str(event.get("trace_id") or "")
            if TRACE_RE.fullmatch(trace) and trace not in traces and len(traces) < 3:
                traces.append(trace)
    stats["top_targets"] = dict(sorted(stats["top_targets"].items(), key=lambda kv: -kv[1])[:8])
    stats["per_minute"] = dict(sorted(stats["per_minute"].items()))
    return {"status": "ok", "query_family": family + ".*", "window_utc": [start.isoformat(), end.isoformat()],
            "events_counted": sum(stats["by_event"].values()), **stats, "sample_trace_ids": traces,
            "note": "Counts from Loki security events. secure-kit events carry part; app events do not."}


def packet(job: dict) -> dict:
    incident = job["incident"]
    if incident["kind"] not in ("error_500", "security_signal"):
        raise ValueError("unsupported incident kind")
    events = job["events"][:5]
    result = {"scope": "isolated synthetic local lab, not production",
              "incident": incident, "recent_events": events,
              "trace_summary": log_evidence(events),
              "limits": "Alert labels are signals, not proof of cause or compromise. Missing logs mean unknown."}
    if incident["kind"] == "error_500":
        result["code_context"] = [snippet(*spec) for spec in SNIPPETS]
        result["code_context_note"] = "Source excerpts are teaching context, not a deployed revision or proven exception line."
    else:
        result["event_statistics"] = event_statistics(incident)
        if not result["trace_summary"]:
            result["trace_summary"] = log_evidence([{"trace_id": trace, "occurred_at": incident["last_seen"]}
                                                    for trace in result["event_statistics"].get("sample_trace_ids", [])])
        result["code_context_note"] = "No defect result is supplied. Propose a human-controlled fake-account test."
    return result


def schema_for(kind: str) -> dict:
    source = RCA_SCHEMA if kind == "error_500" else SECURITY_SCHEMA
    schema = json.loads(json.dumps(source))
    for field in ("observations", "hypotheses", "unknowns", "evidence_refs"):
        schema["properties"][field]["minItems"] = 1
        # validate_*가 거부하는 길이를 모델에게도 미리 알린다(2026-11-15 next_action 500자 초과로 거부된 실측)
        schema["properties"][field]["items"]["maxLength"] = 500
    schema["properties"]["next_action"]["maxLength"] = 500
    return schema


def prompt_for(evidence: dict) -> str:
    kind = evidence["incident"]["kind"]
    focus = ("500 상태, 코드 경로, 로그 신호를 분리하고 원인 가설을 제시하세요. 가격 누락은 가설이며 재현 전 확정하지 마세요."
             if kind == "error_500" else
             "거부 이벤트는 침해 또는 다른 API의 결함 증거가 아닙니다. 정상 반례와 사람의 가짜 계정 확인 시험을 제안하세요.")
    return ("당신은 로컬 교육용 랩의 읽기 전용 조사자입니다. 제공된 사건 자료만 사용하세요. " + focus +
            " 관측 사실, 가설, 미확인을 구분하고 근거에는 사건 번호, trace_id 또는 코드 위치를 쓰세요. "
            "원문 로그, 비밀번호, 토큰, 전화번호, 주소는 쓰지 마세요. 코드 수정과 배포는 하지 마세요. "
            "JSON 스키마에 맞는 객체만 출력하세요.\n\n" + json.dumps(evidence, ensure_ascii=False))


def call_cli(prompt: str, schema: dict) -> tuple[dict, dict]:
    # --setting-sources project,local · --strict-mcp-config: 이 PC의 사용자 플러그인·훅·MCP를 싣지 않는다(ai_fix.py와 같음)
    command = ["claude", "-p", "--tools", "", "--permission-mode", "dontAsk",
               "--no-session-persistence", "--setting-sources", "project,local", "--strict-mcp-config",
               "--max-budget-usd", "0.50",
               "--output-format", "json", "--json-schema", json.dumps(schema), prompt]
    cli_env = os.environ.copy()
    cli_env.pop("ANTHROPIC_API_KEY", None)
    cli_env.pop("ANTHROPIC_AUTH_TOKEN", None)
    result = subprocess.run(command, cwd=HERE, env=cli_env,
                            capture_output=True, text=True, timeout=180)
    if result.returncode:
        tail = (result.stderr.strip() or result.stdout.strip())[-400:]
        raise RuntimeError(f"cli_exit_{result.returncode}: {tail}")
    envelope = json.loads(result.stdout)
    if isinstance(envelope, list):
        envelope = next((item for item in reversed(envelope)
                         if isinstance(item, dict) and item.get("type") == "result"), None)
    if not isinstance(envelope, dict):
        raise ValueError("cli_result_missing")
    report = envelope.get("structured_output")
    if report is None:
        report = json.loads(envelope["result"])
    return report, {"usage": envelope.get("usage"), "total_cost_usd": envelope.get("total_cost_usd")}


def call_api(prompt: str, schema: dict) -> tuple[dict, dict]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("api_key_missing")
    body = {"model": MODEL, "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    request = urllib.request.Request("https://api.anthropic.com/v1/messages", method="POST",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "anthropic-version": API_VERSION,
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            envelope = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"api_http_{exc.code}") from None
    blocks = [part.get("text", "") for part in envelope.get("content", []) if part.get("type") == "text"]
    if not blocks or envelope.get("stop_reason") == "max_tokens":
        raise ValueError("api_result_missing_or_truncated")
    return json.loads("".join(blocks)), {"usage": envelope.get("usage"), "model": envelope.get("model")}


def process_one(backend: str, incident_id: int | None = None) -> dict:
    claim = oncall("POST", "/api/analysis-jobs/claim",
                   {"backend": backend, "incident_id": incident_id})
    job = claim.get("job")
    if job is None:
        return {"status": "no_pending_job"}
    number = job["incident"]["id"]
    try:
        evidence = packet(job)
        schema = schema_for(job["incident"]["kind"])
        report, meta = (call_cli if backend == "cli" else call_api)(prompt_for(evidence), schema)
        try:
            (validate_rca if job["incident"]["kind"] == "error_500" else validate_security)(report)
        except ValueError as exc:
            # 거부한 답도 남겨야 왜 거부됐는지 사람이 볼 수 있다(온콜에는 올리지 않는다)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (HERE / "measurements" / f"local-analysis-rejected-{backend}-{number}-{stamp}.json").write_text(
                json.dumps({"incident_id": number, "rejected_because": str(exc), "model_report": report,
                            "model_metadata": meta}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            raise
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = HERE / "measurements" / f"local-analysis-{backend}-{number}-{stamp}.json"
        target.write_text(json.dumps({"kind": "local_incident_analysis", "backend": backend,
            "incident_id": number, "created_utc": datetime.now(timezone.utc).isoformat(),
            "packet": evidence, "model_report": report, "model_metadata": meta,
            "tools_disabled": True, "human_approval_required_for_fix": True},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        oncall("POST", f"/api/incidents/{number}/analysis", report)
        oncall("POST", f"/api/analysis-jobs/{number}/finish", {"state": "complete"})
        return {"status": "complete", "incident_id": number, "backend": backend,
                "report_file": str(target)}
    except Exception as exc:
        code = re.sub(r"[^a-zA-Z0-9_]", "_", str(exc))[:80] or type(exc).__name__
        oncall("POST", f"/api/analysis-jobs/{number}/finish",
               {"state": "failed", "error_code": code})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("cli", "api"), required=True)
    parser.add_argument("--incident-id", type=int)
    parser.add_argument("--watch", action="store_true", help="Keep polling; run only one local worker")
    parser.add_argument("--retry-failed", type=int, metavar="INCIDENT_ID")
    args = parser.parse_args()
    if args.incident_id is not None and args.incident_id < 1:
        parser.error("incident-id must be positive")
    if args.retry_failed:
        print(json.dumps(oncall("POST", f"/api/analysis-jobs/{args.retry_failed}/retry", {})))
    while True:
        result = process_one(args.backend, args.incident_id)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not args.watch:
            return 0
        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
