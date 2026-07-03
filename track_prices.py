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

Also keeps a rolling 30-day price log per route (to flag "lowest price
seen in 30 days" in alerts) and sends a one-time self-monitoring alert if
a route's price check fails 3 times in a row, so a broken token/API
doesn't fail silently for weeks.

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

CONSECUTIVE_FAILURE_THRESHOLD = 3  # send a self-monitoring alert after this many failed checks in a row
PRICE_LOG_WINDOW_DAYS = 30


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
            _airline_name_cache = {a.get("code"): a.get("name") for a in airlines if a.get("code")}
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
    target_depart_date most closely (within 7 days). If nothing that close
    exists, falls back to the overall cheapest cached fare's airline as an
    approximate guess. Returns (airline_code, is_approximate) or (None, False).
    """
    if not target_depart_date:
        return None, False
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
            return None, False

        target = target_depart_date[:10]  # just the date part, YYYY-MM-DD
        closest_match, closest_diff = None, None
        cheapest_match, cheapest_price = None, None
        for dest_data in data["data"].values():
            for flight in dest_data.values():
                dep = flight.get("departure_at")
                price = flight.get("price")
                if dep:
                    diff = abs((datetime.fromisoformat(dep[:10]) - datetime.fromisoformat(target)).days)
                    if closest_diff is None or diff < closest_diff:
                        closest_diff = diff
                        closest_match = flight
                if price is not None and (cheapest_price is None or price < cheapest_price):
                    cheapest_price = price
                    cheapest_match = flight

        if closest_match and closest_diff is not None and closest_diff <= 7:
            return closest_match.get("airline"), False
        if cheapest_match:
            return cheapest_match.get("airline"), True
    except Exception as e:
        print(f"  Could not look up airline for date (non-fatal): {e}")
    return None, False


def compute_deal_quality(price, price_log):
    """
    Rates today's price against its own recent history in price_log.
    Returns a short label, or None if there isn't enough history yet to
    say anything meaningful (needs at least 3 prior data points).
    """
    prior_prices = [item["price"] for item in price_log if item.get("price") is not None]
    if len(prior_prices) < 3:
        return None

    lowest = min(prior_prices)
    highest = max(prior_prices)
    if highest == lowest:
        return None  # no variation to compare against

    # where today's price falls in the observed range, 0 = cheapest ever seen, 1 = priciest
    position = (price - lowest) / (highest - lowest)
    if position <= 0.15:
        return "\U0001F525 Great deal"
    if position <= 0.45:
        return "\U0001F44D Good deal"
    return "\U0001F610 Fair price"


def update_price_log(entry, price, now_iso):
    """
    Appends today's price to the route's rolling log and prunes entries
    older than PRICE_LOG_WINDOW_DAYS. Returns (updated_log, lowest_price_in_window).
    """
    log = entry.get("price_log", [])
    log.append({"date": now_iso, "price": price})

    cutoff = datetime.now(timezone.utc).timestamp() - (PRICE_LOG_WINDOW_DAYS * 86400)
    pruned = []
    for item in log:
        try:
            item_ts = datetime.fromisoformat(item["date"]).timestamp()
        except Exception:
            continue
        if item_ts >= cutoff:
            pruned.append(item)

    lowest = min((item["price"] for item in pruned), default=price)
    return pruned, lowest


def generate_price_chart(route_name, price_log, currency, target_price):
    """
    Generates a simple line chart of the price log and saves it as a PNG.
    Returns the file path, or None if charting fails or there's not
    enough data to plot (needs at least 2 points) - never raises, since
    a missing chart shouldn't block the rest of the alert.
    """
    if len(price_log) < 2:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")  # no display needed, just save to file
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        dates = [datetime.fromisoformat(item["date"]) for item in price_log]
        prices = [item["price"] for item in price_log]

        fig, ax = plt.subplots(figsize=(7, 3.5))
        ax.plot(dates, prices, marker="o", color="#2481cc", linewidth=2)
        ax.axhline(y=target_price, color="#e74c3c", linestyle="--", linewidth=1, label=f"Target ({target_price})")
        ax.set_title(f"{route_name} - last {len(price_log)} checks")
        ax.set_ylabel(currency.upper())
        ax.legend(loc="upper right", fontsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
        fig.autofmt_xdate()
        fig.tight_layout()

        safe_name = "".join(c if c.isalnum() else "_" for c in route_name)[:40]
        path = Path(f"/tmp/chart_{safe_name}.png")
        fig.savefig(path, dpi=120)
        plt.close(fig)
        return path
    except Exception as e:
        print(f"  Could not generate price chart (non-fatal): {e}")
        return None


def send_telegram_photo(image_path, caption):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption[:1024]},
                files={"photo": f},
                timeout=30,
            )
        resp.raise_for_status()
        print(f"Telegram chart photo sent (status {resp.status_code})")
    except Exception as e:
        print(f"Failed to send Telegram chart photo (non-fatal): {e}")


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
    failure_alerts = []

    for route in routes:
        key = f"{route['origin']}-{route['destination']}-{route['departure_date']}"
        print(f"Checking {route['name']} ({key})...")
        entry = history.get(key, {})

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
                airline_code, airline_approx = get_airline_for_date(
                    route["origin"], route["destination"], route["currency"], best.get("depart_date")
                )
                best["airline"] = airline_code
                best["airline_approximate"] = airline_approx
        except Exception as e:
            print(f"  Failed to fetch price for {key}: {e}")
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            print(f"  Consecutive failures for this route: {entry['consecutive_failures']}")
            if (entry["consecutive_failures"] >= CONSECUTIVE_FAILURE_THRESHOLD
                    and not entry.get("failure_alert_sent")):
                failure_alerts.append(f"{route['name']} ({key}): {entry['consecutive_failures']} checks in a row - last error: {e}")
                entry["failure_alert_sent"] = True
            history[key] = entry
            continue

        if best is None:
            print(f"  No fare data found for {key} (exact or approximate)")
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            if (entry["consecutive_failures"] >= CONSECUTIVE_FAILURE_THRESHOLD
                    and not entry.get("failure_alert_sent")):
                failure_alerts.append(f"{route['name']} ({key}): no fare data found for {entry['consecutive_failures']} checks in a row")
                entry["failure_alert_sent"] = True
            history[key] = entry
            continue

        # success - reset failure tracking
        entry["consecutive_failures"] = 0
        entry["failure_alert_sent"] = False

        price = best["value"]
        target = route["target_price"]
        was_under_target = entry.get("under_target", False)
        airline_code = best.get("airline")
        airline_name = get_airline_name(airline_code) if airline_code else None
        airline_display = f" via {airline_name}" if airline_name else ""

        price_log, lowest_in_window = update_price_log(entry, price, now)
        is_lowest_in_window = price <= lowest_in_window
        trend_display = " (lowest in 30 days)" if is_lowest_in_window else ""
        deal_quality = compute_deal_quality(price, entry.get("price_log", []))
        deal_display = f" [{deal_quality}]" if deal_quality else ""
        print(f"  Cheapest found: {price} {route['currency']}{airline_display} (target: {target}){trend_display}{deal_display}")

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
                "airline_approximate": best.get("airline_approximate", False),
                "is_lowest_in_window": is_lowest_in_window,
                "deal_quality": deal_quality,
                "price_log": price_log,
            })

        entry.update({
            "last_checked": now,
            "last_price": price,
            "under_target": is_under_target,
            "price_log": price_log,
        })
        history[key] = entry

    save_history(history)

    if alerts:
        lines = [f"✈️ Flight price alert: {len(alerts)} route(s) hit your target!\n"]
        for a in alerts:
            note = " (approx. dates - verify before booking)" if a.get("approximate") else ""
            airline_suffix = " (approx.)" if a.get("airline_approximate") else ""
            airline_note = f" | Airline: {a['airline']}{airline_suffix}" if a.get("airline") else ""
            trend_note = " | Lowest in 30 days" if a.get("is_lowest_in_window") else ""
            deal_note = f" | {a['deal_quality']}" if a.get("deal_quality") else ""
            lines.append(
                f"- {a['name']}: {a['price']} {a['currency']} (target {a['target']}) "
                f"| depart {a.get('depart_date', 'N/A')} / return {a.get('return_date', 'N/A')}"
                f"{airline_note}{trend_note}{deal_note}{note}"
            )
        body = "\n".join(lines)
        send_telegram(body)       # primary channel
        send_whatsapp(body)       # secondary, best-effort
        send_email(f"Flight price alert: {len(alerts)} route(s) hit your target", body)

        for a in alerts:
            chart_path = generate_price_chart(a["name"], a.get("price_log", []), a["currency"], a["target"])
            if chart_path:
                send_telegram_photo(chart_path, f"{a['name']} - price trend")
    else:
        print("No new alerts this run.")

    if failure_alerts:
        warning = "\u26a0\ufe0f Flight tracker self-check: repeated failures\n\n" + "\n".join(
            f"- {msg}" for msg in failure_alerts
        ) + "\n\nCheck the GitHub Actions log for details - your TRAVELPAYOUTS_TOKEN or route data may need attention."
        send_telegram(warning)
        send_email("Flight tracker: repeated check failures", warning)


if __name__ == "__main__":
    main()
