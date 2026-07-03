#!/usr/bin/env python3
"""
Weekly Digest
-------------
Sends a summary of all currently tracked routes and their last-known
prices (from history.json), regardless of whether any alert fired. Gives
visibility into price trends even on weeks nothing hits target.

Reuses config/history loading and the send_telegram/send_email functions
from track_prices.py, so it needs no separate credentials setup.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from track_prices import load_config, load_history, send_telegram, send_email, compute_deal_quality  # noqa: E402


def main():
    routes = load_config()
    history = load_history()

    if not routes:
        print("No routes currently tracked - skipping digest.")
        return

    lines = [f"\U0001F4CA Weekly flight price digest ({len(routes)} route(s) tracked)\n"]
    for route in routes:
        key = f"{route['origin']}-{route['destination']}-{route['departure_date']}"
        entry = history.get(key, {})
        last_price = entry.get("last_price")
        last_checked = entry.get("last_checked", "never")

        if last_price is None:
            lines.append(f"- {route['name']}: no price data yet")
            continue

        target = route["target_price"]
        status = "\u2705 under target" if last_price <= target else "above target"
        deal_quality = compute_deal_quality(last_price, entry.get("price_log", []))
        deal_note = f" | {deal_quality}" if deal_quality else ""

        lines.append(
            f"- {route['name']}: {last_price} {route['currency']} "
            f"(target {target}, {status}){deal_note} | last checked {last_checked[:10] if last_checked != 'never' else 'never'}"
        )

    body = "\n".join(lines)
    print(body)
    send_telegram(body)
    send_email("Weekly flight price digest", body)


if __name__ == "__main__":
    main()
