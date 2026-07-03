#!/usr/bin/env python3
"""
Sends the contents of route_confirmation.txt (written by parse_issue_route.py
or remove_route.py) as an actual Telegram message, using the same
send_telegram() function track_prices.py uses for price alerts.

Used by both the Add Route and Remove Route workflows so route changes
made via the Mini App or GitHub Issue Form get confirmed in Telegram, not
just as a GitHub issue comment.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from track_prices import send_telegram  # noqa: E402

CONFIRMATION_PATH = Path(__file__).parent.parent / "route_confirmation.txt"


def main():
    if not CONFIRMATION_PATH.exists():
        print("No route_confirmation.txt found - nothing to send.")
        return
    text = CONFIRMATION_PATH.read_text().strip()
    if not text:
        print("route_confirmation.txt is empty - nothing to send.")
        return
    send_telegram(f"\U0001F4CD Route update\n\n{text}")


if __name__ == "__main__":
    main()
