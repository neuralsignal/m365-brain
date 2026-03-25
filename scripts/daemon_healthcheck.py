"""Daemon health check script for Docker HEALTHCHECK.

Reads state/daemon_health.json and exits non-zero if the last cycle
timestamp is older than the staleness threshold.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

HEALTH_FILE = Path("/app/state/daemon_health.json")
MAX_STALE_SECONDS = 300  # 5 minutes


def main() -> None:
    if not HEALTH_FILE.exists():
        # No health file yet — daemon hasn't completed its first cycle
        print(f"UNHEALTHY: {HEALTH_FILE} does not exist")
        sys.exit(1)

    data = json.loads(HEALTH_FILE.read_text())
    last_ts = data.get("last_cycle_completed")
    if last_ts is None:
        print("UNHEALTHY: no last_cycle_completed in health file")
        sys.exit(1)

    last_dt = datetime.fromisoformat(last_ts)
    age = (datetime.now(tz=UTC) - last_dt).total_seconds()

    if age > MAX_STALE_SECONDS:
        print(f"UNHEALTHY: last cycle {age:.0f}s ago (threshold: {MAX_STALE_SECONDS}s)")
        sys.exit(1)

    print(f"HEALTHY: last cycle {age:.0f}s ago")
    sys.exit(0)


if __name__ == "__main__":
    main()
