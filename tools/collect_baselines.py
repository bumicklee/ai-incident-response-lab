"""Run the remaining baseline candidates sequentially and stop at first invalid run."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from run_stages import HERE


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=5)
    args = ap.parse_args()
    if not 1 <= args.start <= args.end <= 5:
        ap.error("start/end must be ascending run numbers in 1..5")
    for number in range(args.start, args.end + 1):
        existing = [path for path in (HERE / "measurements").glob(f"baseline-candidate-r{number}-*.json")
                    if json.loads(path.read_text(encoding="utf-8")).get("kind") == "baseline_candidate"]
        if existing:
            raise SystemExit(f"run {number}: 정식 후보 파일이 이미 있습니다. 검사·정리 없이 덮어쓰지 않습니다")
        print(f"기준선 {number}/5 시작 (30분)", flush=True)
        subprocess.run([sys.executable, str(HERE / "tools/traffic_baseline.py"),
                        "--seconds", "1800", "--rps", "2", "--run", str(number)], check=True)
        candidates = [path for path in (HERE / "measurements").glob(f"baseline-candidate-r{number}-*.json")
                      if json.loads(path.read_text(encoding="utf-8")).get("kind") == "baseline_candidate"]
        if len(candidates) != 1:
            raise SystemExit(f"run {number}: 완료 파일 수가 1개가 아닙니다")
        data = json.loads(candidates[0].read_text(encoding="utf-8"))
        issues = []
        if data["overall"]["requests"] != 3600 or len(data["samples"]) != 3600:
            issues.append("요청 수가 3,600건이 아님")
        if not 1799 <= data["duration_actual_seconds"] <= 1830:
            issues.append("실제 시간이 1799..1830초 범위 밖")
        if len(data["five_minute_bins"]) != 6 or any(not 590 <= b["requests"] <= 610
                                                         for b in data["five_minute_bins"]):
            issues.append("5분 구간 수 또는 요청 수 이상")
        if data.get("unexpected_results") != 0:
            issues.append(f"예상과 다른 응답 {data.get('unexpected_results')}건")
        if data.get("auth_refresh_interval_seconds") != 600 or len(data.get("auth_refresh_at_seconds", [])) != 2:
            issues.append("10분 간격 토큰 갱신 2회가 아님")
        if set(data.get("loki", {})) != {"security", "error"} or any(
                "query_error" in value or value.get("possibly_truncated") for value in data.get("loki", {}).values()):
            issues.append("Loki 조회 누락·실패·잘림")
        print(json.dumps({"run": number, "file": candidates[0].name, "issues": issues}, ensure_ascii=False), flush=True)
        if issues:
            raise SystemExit(f"run {number}: 품질 검사 실패. 다음 회차를 시작하지 않습니다")
    if args.end == 5:
        subprocess.run([sys.executable, str(HERE / "tools/summarize_baselines.py")], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
