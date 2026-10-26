#!/usr/bin/env python3
"""Smart Render Keep-Alive Pinger (College Hours Only).

Prevents Render's 15-minute sleep during class hours (Mon-Fri 8:30 AM - 6:30 PM)
while letting it sleep at night and on weekends to conserve free tier hours.

Monthly Usage Calculation:
- 10 hours/day * 22 weekdays = ~220 hours/month.
- Render Free Tier = 750 hours/month.
- Remaining Surplus = 530+ hours/month (Zero chance of monthly suspension).
"""

import sys
import time
import argparse
from datetime import datetime
import urllib.request
import urllib.error

TARGET_URL = "https://class-tracker-pgo5.onrender.com/healthz"
START_HOUR = 8    # 8:30 AM
START_MINUTE = 30
END_HOUR = 18     # 6:30 PM
END_MINUTE = 30
PING_INTERVAL_SECONDS = 600  # 10 minutes


def is_active_window(now: datetime) -> bool:
    """Returns True if current time is Monday-Friday between 8:30 AM and 6:30 PM."""
    # 0 = Monday, 4 = Friday, 5 = Saturday, 6 = Sunday
    if now.weekday() >= 5:
        return False

    current_minutes = now.hour * 60 + now.minute
    start_minutes = START_HOUR * 60 + START_MINUTE
    end_minutes = END_HOUR * 60 + END_MINUTE

    return start_minutes <= current_minutes <= end_minutes


def ping(url: str) -> bool:
    """Sends a lightweight GET request to the target URL."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ClassTrackerSmartPinger/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.getcode()
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ Ping SUCCESS -> HTTP {code}")
            return code == 200
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚠️ Ping WARNING -> {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Smart Render Keep-Alive Pinger")
    parser.add_argument("--url", default=TARGET_URL, help="Target healthcheck URL")
    parser.add_argument("--force", action="store_true", help="Ping once immediately regardless of schedule")
    args = parser.parse_args()

    if args.force:
        print(f"⚡ Force-pinging {args.url} now...")
        ping(args.url)
        return

    print("=" * 60)
    print("Class Tracker Smart Keep-Alive Pinger")
    print(f"Target: {args.url}")
    print(f"Active Schedule: Mon-Fri {START_HOUR:02d}:{START_MINUTE:02d} - {END_HOUR:02d}:{END_MINUTE:02d}")
    print(f"Ping Interval: Every {PING_INTERVAL_SECONDS // 60} minutes")
    print("=" * 60)

    while True:
        now = datetime.now()
        if is_active_window(now):
            ping(args.url)
            time.sleep(PING_INTERVAL_SECONDS)
        else:
            print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 🌙 Outside active class hours. Letting Render sleep to conserve free hours.")
            # Sleep 15 minutes before checking schedule again
            time.sleep(900)


if __name__ == "__main__":
    main()
