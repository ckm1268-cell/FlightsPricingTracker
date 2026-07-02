#!/usr/bin/env python3
"""
Flight Price Tracker
---------------------
Checks fares for specific departure/return dates (via the Travelpayouts
week-matrix API, which returns prices for a 7-day window around the dates
you give it) against target prices in config.yaml, and sends a Telegram
alert (primary) plus a WhatsApp alert via CallMeBot (secondary, best-effort
- CallMeBot is a free hobby service and can be unreliable, so failures here
never block the Telegram alert) - and optionally an email - when a target
is met.

Required environment variables (set as GitHub Actions secrets):
  TRAVELPAYOUTS_TOKEN  - free token from https://www.travelpayouts.com

  TELEGRAM_BOT_TOKEN   - token from @BotFather (see README)
  TELEGRAM_CHAT_ID     - your personal chat ID (see README)

Optional:
  CALLMEBOT_PHONE, CALLMEBOT_APIKEY  - WhatsApp via CallMeBot (see README)
  GMAIL_USER, GMAIL_APP_PASSWORD, NOTIFY_EMAIL  - email backup channel
"""

import os
import json
import smtplib
import sys
from email.mime.text import MIMEText
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import quote

import requests
import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"
HISTORY_PATH = Path(__file__).parent / "history.json"

TRAVELPAYOUTS_TOKEN = os.environ.get("TRAVELPAYOUTS_TOKEN")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY")

GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", GMAIL_USER)

WEEK_MATRIX_URL = "https://api.travelpayouts.com/v2/prices/week-matrix"
CHEAP_URL = "https://api.travelpayouts.com/v1/prices/cheap"
AIRLINES_URL = "https://api.travelpayouts.com/data/en/airlines.json"

_airline_name_cache = None  # populated once per run, on first lookup


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


def get_cheapest_month_price(origin, destination, currency):
    """
    Fallback for routes with no cached week-matrix data. Queries the
    prices/cheap endpoint, which has broader coverage (any cheapest fare
    found for the route recently, regardless of specific dates). Less
    precise than week-matrix (dates may not match what you asked for) but
    better than nothing for less-popular routes.
    """
    params = {
        "origin": origin,
        "destination": destination,
        "currency": currency,
        "token": TRAVELPAYOUTS_TOKEN,
    }
    resp = requests.get(CHEAP_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("success") or not data.get("data"):
        return None

    best = None
    for dest_data in data["data"].values():
        for flight in dest_data.values():
            price = flight.get("price")
            if price is not None and (best is None or price < best["value"]):
                best = {
                    "value": price,
                    "depart_date": flight.get("departure_at"),
                    "return_date": flight.get("return_at"),
                    "airline": flight.get("airline"),  # IATA code, e.g. "MH"
                    "approximate": True,
                }
    return best


def get_airline_name(iata_code):
    """
    Resolves a 2-letter IATA airline code (e.g. "MH") to a readable name
    (e.g. "Malaysia Airlines"). Fetches Travelpayouts' static airline list
    once per run and caches it in memory. Falls back to the raw code if
    the lookup fails or the code isn't found - never raises.
    """
    global _airline_name_cache
    if not iata_code:
        return None

    if _airline_name_cache is None:
        try:
            resp = requests.get(AIRLINES_URL, params={"token": TRAVELPAYOUTS_TOKEN}, timeout=15)
            resp.raise_for_status()
            airlines = resp.json()
            _airline_name_cache = {a.get("iata"): a.get("name") for a in airlines if a.get("iata")}
            print(f"  Loaded {len(_airline_name_cache)} airline names")
        except Exception as e:
            print(f"  Could not load airline name list (non-fatal): {e}")
            _airline_name_cache = {}

    return _airline_name_cache.get(iata_code, iata_code)


def get_airline_for_date(origin, destination, currency, target_depart_date):
    """
    Best-effort lookup for which airline is behind a given price, used to
    enrich week-matrix results (which don't include an airline field).
    Queries prices/cheap and picks the entry whose departure date matches
    target_depart_date most closely. Returns an IATA airline code, or None
    if nothing useful is found.
    """
    if not target_depart_date:
        return None
    try:
        params = {
            "origin": origin,
            "destination": destination,
            "currency": currency,
            "token": TRAVELPAYOUTS_TOKEN,
        }
        resp = requests.get(CHEAP_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success") or not data.get("data"):
            return None

        target = target_depart_date[:10]  # just the date part, YYYY-MM-DD
        best_match = None
        best_diff = None
        for dest_data in data["data"].values():
            for flight in dest_data.values():
                dep = flight.get("departure_at")
                if not dep:
                    continue
                diff = abs((datetime.fromisoformat(dep[:10]) - datetime.fromisoformat(target)).days)
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best_match = flight

        if best_match and best_diff is not None and best_diff <= 3:
            return best_match.get("airline")
    except Exception as e:
        print(f"  Could not look up airline for date (non-fatal): {e}")
    return None


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


def send_whatsapp(text):
    if not (CALLMEBOT_PHONE and CALLMEBOT_APIKEY):
        print("CallMeBot credentials missing - skipping WhatsApp send (optional channel). "
              "Set CALLMEBOT_PHONE and CALLMEBOT_APIKEY as secrets to enable it.")
        return
    url = (
        "https://api.callmebot.com/whatsapp.php"
        f"?phone={quote(CALLMEBOT_PHONE)}&text={quote(text)}&apikey={CALLMEBOT_APIKEY}"
    )
    try:
        resp = requests.get(url, timeout=30)
        print(f"WhatsApp send status: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        # Best-effort only - CallMeBot is a free hobby service and can be
        # flaky. Never let a WhatsApp failure block the Telegram alert.
        print(f"Failed to send WhatsApp message (non-fatal, Telegram is primary): {e}")


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
            if best is None:
                print(f"  No exact-date data for {key}, trying broader monthly lookup...")
                best = get_cheapest_month_price(route["origin"], route["destination"], route["currency"])
            elif "airline" not in best:
                # week-matrix doesn't return airline info - try a best-effort lookup
                best["airline"] = get_airline_for_date(
                    route["origin"], route["destination"], route["currency"], best.get("depart_date")
                )
        except Exception as e:
            print(f"  Failed to fetch price for {key}: {e}")
            continue

        if best is None:
            print(f"  No fare data found for {key} (exact or approximate)")
            continue

        price = best["value"]
        target = route["target_price"]
        was_under_target = history.get(key, {}).get("under_target", False)
        airline_code = best.get("airline")
        airline_name = get_airline_name(airline_code) if airline_code else None
        airline_display = f" via {airline_name}" if airline_name else ""
        print(f"  Cheapest found: {price} {route['currency']}{airline_display} (target: {target})")

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
                "approximate": best.get("approximate", False),
                "airline": airline_name,
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
            note = " (approx. dates - verify before booking)" if a.get("approximate") else ""
            airline_note = f" | Airline: {a['airline']}" if a.get("airline") else ""
            lines.append(
                f"- {a['name']}: {a['price']} {a['currency']} (target {a['target']}) "
                f"| depart {a.get('depart_date', 'N/A')} / return {a.get('return_date', 'N/A')}"
                f"{airline_note}{note}"
            )
        body = "\n".join(lines)
        send_telegram(body)       # primary channel
        send_whatsapp(body)       # secondary, best-effort
        send_email(f"Flight price alert: {len(alerts)} route(s) hit your target", body)
    else:
        print("No new alerts this run.")


if __name__ == "__main__":
    main()
