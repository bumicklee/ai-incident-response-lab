"""Turn the published measurement checklist into a pending JSON manifest."""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "실측-준비-목록.md"
TARGET = ROOT / "measurements" / "plan.json"
ROW = re.compile(r"^\|\s*(\d-\d+)\s*\|\s*([^|]+)\|")


def main() -> None:
    prior = {}
    if TARGET.exists():
        prior_data = json.loads(TARGET.read_text(encoding="utf-8"))
        prior = {item["id"]: item for item in prior_data.get("items", [])}
    items = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        match = ROW.match(line)
        if match:
            item_id = match.group(1)
            old = prior.get(item_id, {})
            items.append({"id": item_id, "name": match.group(2).strip(),
                          "status": old.get("status", "pending"),
                          "runs": old.get("runs", []),
                          "notes": old.get("notes", [])})
    if len(items) < 40:
        raise SystemExit(f"측정 항목 파싱 결과가 너무 적습니다: {len(items)}")
    TARGET.parent.mkdir(exist_ok=True)
    TARGET.write_text(json.dumps({"source": str(SOURCE.relative_to(ROOT.parent)), "items": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(items)}개 측정 항목(기존 진행 상태 보존): {TARGET}")


if __name__ == "__main__":
    main()
