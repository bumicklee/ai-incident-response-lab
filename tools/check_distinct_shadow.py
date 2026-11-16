"""Check the live Loki distinct-target query and Grafana shadow rule.

The scenario JSON labels the synthetic user's intent. Loki only sees HTTP
events, so the normal/attack label is evidence supplied by the test runner.
No password, token, response body, or raw log is written to the result.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


LAB = Path(__file__).resolve().parents[1]
LOKI = "http://127.0.0.1:13100/loki/api/v1/query"
BASE = '{app="mall-portal",stream="security",event="access.denied.not_owner"} | json | actor!="" | target!=""'
QUERIES = {
    "denied_total_1m": f"sum by (actor) (count_over_time({BASE} [1m]))",
    "denied_total_10m": f"sum by (actor) (count_over_time({BASE} [10m]))",
    "distinct_denied_targets_10m":
        f"count by (actor) (sum by (actor, target) (count_over_time({BASE} [10m])))",
}


def env_value(name: str, default: str = "") -> str:
    for line in (LAB / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    return default


def load_json(url: str, *, user: str = "", password: str = "") -> dict | list:
    headers = {}
    if user:
        credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = "Basic " + credentials
    with urlopen(Request(url, headers=headers), timeout=20) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario_json", type=Path)
    parser.add_argument("--require-delivery", action="store_true",
                        help="Grafana 섀도 웹훅이 온콜 섀도 기록에 도착했는지도 요구")
    args = parser.parse_args()
    scenario = json.loads(args.scenario_json.read_text(encoding="utf-8"))
    if scenario.get("scenario") not in ("idor", "shared-links"):
        parser.error("idor 또는 shared-links 시나리오 JSON이 필요합니다")
    ended = datetime.fromisoformat(scenario["ended_utc"])
    query_time = (ended + timedelta(seconds=2)).isoformat()
    results = {}
    for name, expression in QUERIES.items():
        url = LOKI + "?" + urlencode({"query": expression, "time": query_time})
        payload = load_json(url)
        if payload.get("status") != "success":
            raise RuntimeError(f"Loki query failed: {name}")
        results[name] = {item["metric"].get("actor", ""): int(float(item["value"][1]))
                         for item in payload["data"]["result"]}

    grafana = load_json("http://127.0.0.1:13030/api/v1/provisioning/alert-rules",
                        user="admin", password=env_value("LAB_GRAFANA_PASSWORD", "sre-lab-grafana-2026"))
    rule = next((item for item in grafana if item.get("uid") == "sre_distinct_denied_shadow"), None)
    actor = "member:102"  # The seeded Bob account used by both bounded scenarios.
    observed = {name: values.get(actor, 0) for name, values in results.items()}
    shadow_response = load_json("http://127.0.0.1:18090/api/shadow-alerts",
                                user=env_value("LAB_ONCALL_VIEW_USER"),
                                password=env_value("LAB_ONCALL_VIEW_PASSWORD"))
    shadow_seen = any(item.get("actor") == actor
                      and item.get("rule_name") == "DistinctDeniedOrdersShadow"
                      and item.get("starts_at", "") >= scenario["started_utc"]
                      for item in shadow_response["alerts"])
    result = {
        "kind": "live_loki_distinct_shadow_check",
        "scenario_file": args.scenario_json.name,
        "ground_truth": scenario.get("ground_truth", "scenario_only"),
        "query_time_utc": query_time,
        "actor": actor,
        "counts": observed,
        "grafana_shadow_rule_present": rule is not None,
        "grafana_shadow_rule_paused": rule.get("isPaused") if rule else None,
        "would_fire_at_threshold_3": observed["distinct_denied_targets_10m"] >= 3,
        "shadow_webhook_recorded": shadow_seen,
        "limitations": ["historical Loki query, not Grafana notification delivery time",
                        "same seeded actor can overlap with other runs in a 10-minute window",
                        "normal intent is a synthetic scenario label, not visible to Loki"],
    }
    target = LAB / "measurements" / f"distinct-shadow-{scenario['run_id']}.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"file": str(target), "counts": observed,
                      "grafana_shadow_rule_present": rule is not None,
                      "would_fire_at_threshold_3": result["would_fire_at_threshold_3"],
                      "shadow_webhook_recorded": shadow_seen}, ensure_ascii=False))
    passed = rule and observed["distinct_denied_targets_10m"] >= 3
    return 0 if passed and (shadow_seen or not args.require_delivery) else 2


if __name__ == "__main__":
    raise SystemExit(main())
