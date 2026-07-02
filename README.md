# Flight Price Tracker

Free, always-on flight price tracker. Checks fares every 6 hours via GitHub
Actions, sends a **Telegram alert** when a route hits your target price,
and lets you add new routes by filling out a form in the GitHub app -
no need to hand-edit any files.

## How it works
- **Add a route:** open a new Issue using the "✈️ Add a flight route to
  track" form (works great from the GitHub mobile app). A workflow parses
  it and appends it to `config.yaml` automatically, then closes the issue
  with a confirmation.
- **Checks run automatically:** every 3 hours, `track_prices.py` fetches
  the cheapest fare for each route's dates (Travelpayouts week-matrix API
  - checks a 7-day window around your dates, since fares move day to day)
  and compares it to your target.
- **Alerts:** sent to your Telegram via the official Telegram Bot API when
  a price newly drops to/below target (you won't get repeat alerts every
  run for the same dip - only when it crosses below target again after
  rising above it).
- `history.json` — auto-generated, tracks each route's last price so
  alerts aren't repeated unnecessarily.

## One-time setup

### 1. Get a free Travelpayouts token
1. Sign up at https://www.travelpayouts.com (free, no payment info needed).
2. Go to your account → "API" / "Data API" section and copy your token.

### 2. Set up Telegram alerts (official Bot API - free, no rate limit issues)
1. In Telegram, search for **@BotFather** and start a chat.
2. Send `/newbot`, give it a name and a username (must end in "bot", e.g.
   `flightpricealerts_bot`). BotFather replies with a **token** - save it,
   that's your `TELEGRAM_BOT_TOKEN`.
3. Search for the bot you just created (by the username you gave it) and
   send it any message, e.g. "hi", to start a chat with it.
4. Get your chat ID: open this URL in a browser (replace `<TOKEN>` with
   your bot token):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Find `"chat":{"id":` in the response - that number is your
   `TELEGRAM_CHAT_ID`.
   (If the response is empty, make sure you sent the bot a message first,
   then reload the URL.)

### 3. (Optional) Email as a backup channel
If you also want email alerts, follow the Gmail App Password steps:
1. Turn on 2-Step Verification: https://myaccount.google.com/security
2. Generate an App Password (choose "Mail"): https://myaccount.google.com/apppasswords

### 4. Create the GitHub repo
1. Create a **public** repo (e.g. `flight-price-tracker`) and push these
   files to it, including the `.github` folder.
2. Go to repo **Settings → Secrets and variables → Actions → New repository
   secret** and add:
   - `TRAVELPAYOUTS_TOKEN` — your Travelpayouts token
   - `TELEGRAM_BOT_TOKEN` — the token BotFather gave you
   - `TELEGRAM_CHAT_ID` — your chat ID from the getUpdates step
   - *(optional)* `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `NOTIFY_EMAIL`

### 5. Add your first route
Go to the **Issues** tab → **New Issue** → "✈️ Add a flight route to
track" → fill in the form → Submit. Within a minute or two you'll see a
confirmation comment and the issue will auto-close. Repeat for as many
routes as you like.

### 6. Test the price check
Go to the **Actions** tab → "Flight Price Check" workflow → "Run workflow"
to trigger it manually and confirm everything works end-to-end.

## Editing or removing routes
Right now the issue form only *adds* routes. To edit or remove one, just
edit `config.yaml` directly in the repo (each route is a plain YAML block).

## Adjusting the check frequency
Edit the cron schedule in `.github/workflows/check_prices.yml`. Default is
every 6 hours. Cron syntax: `min hour day month weekday`.

## Costs
Everything here is free:
- GitHub Actions: unlimited minutes on public repos.
- Travelpayouts Data API: free tier.
- Telegram Bot API: free, official, no usage limits for personal bots.
- Gmail SMTP (optional): free, uses your own Gmail account.
