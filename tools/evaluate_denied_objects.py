"""Offline teaching fixture for denied-order rule comparison.

This does not query Loki, send requests, or measure Grafana/phone delay.
It creates *new* synthetic data; numbers are not from llm-security-3.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
START = datetime(2026, 11, 14, tzinfo=timezone.utc)
WINDOW_SECONDS = 600


def fixture() -> tuple[list[dict], dict[str, str]]:
    rows: list[dict] = []
    labels: dict[str, str] = {}

    def add(actor: str, group: str, minute: float, target: str, denied: bool,
            ip: str = "198.51.100.10") -> None:
        labels[actor] = group
        rows.append({"actor": actor, "label": group, "minute": minute,
                     "ts": (START + timedelta(minutes=minute)).isoformat(),
                     "target": target, "denied": denied, "ip": ip})

    # Every scenario is deliberately labelled. Allowed requests do not enter
    # the denied-object rule but keep the normal traffic visibly imperfect.
    for n in range(20):
        add("normal-busy", "normal", n * 0.4, f"order:own-{n}", False)
    add("normal-busy", "normal", 8.5, "order:family-link", True)
    for n in range(30):
        add("normal-poll", "normal", n * 0.2, "order:own-poll", False)
    for n in range(8):
        add("normal-buggy", "normal", n * 0.1, "order:old-1", True)
    for n in range(5):
        add("normal-deleted", "normal", n * 0.15, "order:deleted-2", True)
    # A legitimate user opens three different old family-shared links.
    # Distinct target counting can also alert on normal behavior.
    for n, minute in enumerate((1.0, 4.0, 7.0)):
        add("normal-family-history", "normal", minute,
            f"order:old-family-{n}", True)
    # Fifty distinct users behind one IP: per-IP counting would merge them.
    for n in range(50):
        add(f"normal-cgnat-{n:02d}", "normal", n * 0.1,
            f"order:family-{n}", True, ip="198.51.100.50")
    for n in range(4):
        add("attack-fast", "attack", n * 0.2, f"order:foreign-fast-{n}", True)
        add("attack-slow", "attack", n * 3.0, f"order:foreign-slow-{n}", True)
    # Rotating accounts deliberately evades an account-scoped threshold.
    for n in range(4):
        add(f"attack-rotated-{n}", "attack", n * 1.0,
            f"order:foreign-rotated-{n}", True)
    for n in range(2):
        add("attack-low-volume", "attack", n * 2.0,
            f"order:foreign-low-{n}", True)
    return sorted(rows, key=lambda row: row["minute"]), labels


def first_hits(rows: list[dict], threshold: int, distinct: bool,
               window_seconds: int) -> dict[str, dict]:
    hits: dict[str, dict] = {}
    denied: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if not row["denied"]:
            continue
        actor = row["actor"]
        denied[actor].append(row)
        recent = [item for item in denied[actor]
                  if row["minute"] - item["minute"] < window_seconds / 60]
        count = len({item["target"] for item in recent}) if distinct else len(recent)
        if count >= threshold and actor not in hits:
            hits[actor] = {"first_hit_minute": row["minute"],
                           "first_hit_request": len(denied[actor]),
                           "window_count": count}
    return hits


def summarize(rows: list[dict], labels: dict[str, str], threshold: int,
              distinct: bool, window_seconds: int) -> dict:
    hits = first_hits(rows, threshold, distinct, window_seconds)
    attack_actors = {actor for actor, label in labels.items() if label == "attack"}
    normal_actors = {actor for actor, label in labels.items() if label == "normal"}
    def scenario(actor: str) -> str:
        return "attack-rotated" if actor.startswith("attack-rotated-") else actor
    attack_scenarios = {scenario(actor) for actor in attack_actors}
    flagged_scenarios = {scenario(actor) for actor in attack_actors & hits.keys()}
    return {"rule": "distinct_denied_targets" if distinct else "denied_total",
            "threshold_per_actor": threshold,
            "window_seconds": window_seconds,
            "attack_scenarios_flagged": sorted(flagged_scenarios),
            "attack_scenarios_missed": sorted(attack_scenarios - flagged_scenarios),
            "attack_scenarios_total": len(attack_scenarios),
            "attacker_accounts_flagged": len(attack_actors & hits.keys()),
            "attacker_accounts_total": len(attack_actors),
            "normal_users_flagged": len(normal_actors & hits.keys()),
            "normal_users_total": len(normal_actors),
            "first_hits": hits,
            "limitations": ["offline invented fixture, not Spring/Loki measurement",
                            "account rotation appears as four separate actors",
                            "no real HTTP latency or Grafana delivery measurement"]}


def main() -> None:
    rows, labels = fixture()
    burst = summarize(rows, labels, threshold=3, distinct=False, window_seconds=60)
    total = summarize(rows, labels, threshold=3, distinct=False,
                      window_seconds=WINDOW_SECONDS)
    unique = summarize(rows, labels, threshold=3, distinct=True,
                       window_seconds=WINDOW_SECONDS)
    assert burst["attack_scenarios_flagged"] == ["attack-fast"]
    assert burst["normal_users_flagged"] == 2
    assert "normal-family-history" not in burst["first_hits"]
    assert "normal-family-history" in total["first_hits"]
    assert "normal-family-history" in unique["first_hits"]
    assert "normal-buggy" not in unique["first_hits"]
    assert unique["attacker_accounts_flagged"] == 2
    assert unique["attack_scenarios_flagged"] == ["attack-fast", "attack-slow"]
    out = {"kind": "offline_denied_object_teaching_fixture_not_live",
           "source": "new deterministic fixture inspired by llm-security-3 scenarios; not its results",
           "events": rows, "rules": [burst, total, unique]}
    target = HERE / "measurements" / "denied-object-offline-fixture.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps({"file": str(target), "events": len(rows),
                      "rules": [{k: value[k] for k in ("rule", "attack_scenarios_flagged",
                                                       "normal_users_flagged")}
                                for value in out["rules"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
