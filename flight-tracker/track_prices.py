#!/usr/bin/env python3
"""
Flight Price Tracker
---------------------
Checks fares for specific departure/return dates (via the Travelpayouts
week-matrix API, which returns prices for a 7-day window around the dates
you give it) against target prices in config.yaml, and sends a Telegram
alert - and optionally an email - when a target is met.

Required environment variables (set as GitHub Actions secrets):
  TRAVELPAYOUTS_TOKEN  - free token from https://www.travelpayouts.com

  TELEGRAM_BOT_TOKEN   - token from @BotFather (see README)
  TELEGRAM_CHAT_ID     - your personal chat ID (see README)

Optional (only used if all three are set - keeps email as a backup channel):
  GMAIL_USER, GMAIL_APP_PASSWORD, NOTIFY_EMAIL
"""

import os
import json
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timezone

import requests
import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"
HISTORY_PATH = Path(__file__).parent / "history.json"

TRAVELPAYOUTS_TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)

WEEK_MATRIX_URL = "https://api.travelpayouts.com/v2/prices/week-matrix"


def load_config():
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
        return data.get("routes", [])


def load_history():
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return {}


def save_history(history):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)


def get_best_price(origin, destination, departure_date, return_date, currency):
    """
    Query Travelpayouts week-matrix for the cheapest fare within a 7-day
    window centered on the given departure/return dates.
    """
    params = {
        "currency": currency,
        "origin": origin,
        "destination": destination,
        "depart_date": departure_date,
        "show_to_affiliates": "true",
        "token": TRAVELPAYOUTS_TOKEN,
    }
    if return_date:
        params["return_date"] = return_date

    resp = requests.get(WEEK_MATRIX_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success") or not data.get("data"):
        return None

    entries = data["data"]
    if isinstance(entries, dict):
        entries = list(entries.values())

    best = None
    for entry in entries:
        value = entry.get("value")
        if value is not None and (best is None or value < best["value"]):
            best = entry
    return best


def send_telegram(text):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("Telegram credentials missing - skipping send. "
              "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as secrets.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=30)
        resp.raise_for_status()
        print(f"Telegram message sent (status {resp.status_code})")
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")


def send_email(subject, body):
    if not (GMAIL_USER and GMAIL_APP_PASSWORD and NOTIFY_EMAIL):
        return  # email is optional/backup - silently skip if not configured

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)
    print(f"Email sent to {NOTIFY_EMAIL}: {subject}")


def main():
    if not TRAVELPAYOUTS_TOKEN:
        print("ERROR: TRAVELPAYOUTS_TOKEN is not set.", file=sys.stderr)
        sys.exit(1)

    routes = load_config()
    history = load_history()
    now = datetime.now(timezone.utc).isoformat()
    alerts = []

    for route in routes:
        key = f"{route['origin']}-{route['destination']}-{route['departure_date']}"
        print(f"Checking {route['name']} ({key})...")

        try:
            best = get_best_price(
                route["origin"],
                route["destination"],
                route["departure_date"],
                route.get("return_date") or None,
                route["currency"],
            )
        except Exception as e:
            print(f"  Failed to fetch price for {key}: {e}")
            continue

        if best is None:
            print(f"  No fare data found for {key}")
            continue

        price = best["value"]
        target = route["target_price"]
        was_under_target = history.get(key, {}).get("under_target", False)
        print(f"  Cheapest found: {price} {route['currency']} (target: {target})")

        is_under_target = price <= target

        # Only alert on a fresh drop below target (avoids repeat spam every run)
        if is_under_target and not was_under_target:
            alerts.append({
                "name": route["name"],
                "price": price,
                "currency": route["currency"],
                "target": target,
                "depart_date": best.get("depart_date"),
                "return_date": best.get("return_date"),
            })

        history[key] = {
            "last_checked": now,
            "last_price": price,
            "under_target": is_under_target,
        }

    save_history(history)

    if alerts:
        lines = [f"✈️ Flight price alert: {len(alerts)} route(s) hit your target!\n"]
        for a in alerts:
            lines.append(
                f"- {a['name']}: {a['price']} {a['currency']} (target {a['target']}) "
                f"| depart {a.get('depart_date', 'N/A')} / return {a.get('return_date', 'N/A')}"
            )
        body = "\n".join(lines)
        send_telegram(body)
        send_email(f"Flight price alert: {len(alerts)} route(s) hit your target", body)
    else:
        print("No new alerts this run.")


if __name__ == "__main__":
    main()
