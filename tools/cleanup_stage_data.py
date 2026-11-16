"""Delete only return applications tagged by a particular stage run."""

import argparse
import json
from pathlib import Path
import re
import subprocess

LAB = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage_json", type=Path, nargs="?", help="생략하면 가장 최근 레드 단계 JSON")
    args = parser.parse_args()
    candidates = sorted((LAB / "measurements").glob("stages-red-*.json"))
    stage_json = args.stage_json or (candidates[-1] if candidates else None)
    if not stage_json:
        parser.error("레드 단계 JSON이 없습니다")
    evidence = json.loads(stage_json.read_text())
    marker = evidence["cleanup_return_marker"]
    if not re.fullmatch(r"SRE-LAB-\d{8}T\d{6}Z-(red|green)", marker):
        raise ValueError("Unexpected marker; refusing cleanup")
    sql = (f"SELECT COUNT(*) FROM oms_order_return_apply WHERE description = '{marker}';\n"
           f"DELETE FROM oms_order_return_apply WHERE description = '{marker}';\n"
           f"SELECT COUNT(*) FROM oms_order_return_apply WHERE description = '{marker}';\n")
    result = subprocess.run(
        ["docker", "compose", "-f", str(LAB / "compose.yaml"), "exec", "-T", "mysql",
         "sh", "-c", 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" mall -N'],
        input=sql, text=True, capture_output=True, check=True)
    counts = [int(x) for x in result.stdout.splitlines()]
    if len(counts) != 2 or counts[1] != 0:
        raise RuntimeError(f"Cleanup unverified: {counts}")
    print(json.dumps({"stage_file": str(stage_json), "matching_rows_before": counts[0],
                      "matching_rows_after": counts[1]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
