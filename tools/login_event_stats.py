#!/usr/bin/env python3
"""Count login security events in Loki for the last N minutes (secure-kit unit, statistics step).

    python3 tools/login_event_stats.py --minutes 15        (make kit-stats)

Prints counts by event, source (app or secure-kit part), reason, target and minute. No raw log lines.
The same function feeds the AI analysis packet (tools/local_analysis_worker.py event_statistics).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from local_analysis_worker import event_statistics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=int, default=15)
    parser.add_argument("--event", default="auth.login.rate_limited", help="event name whose family is counted")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    # event_statistics looks 10 minutes before first_seen; shift first_seen so the window is the last N minutes
    first = now - timedelta(minutes=max(args.minutes - 10, 0))
    stats = event_statistics({"fingerprint": args.event, "first_seen": first.isoformat(), "last_seen": now.isoformat()})
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
