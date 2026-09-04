# Flight Price Tracker

A free, self-hosted flight price tracking system. Monitors airfares for
your chosen routes (including nearby alternative airports), tells you
whether prices are trending up or down, gives a plain-language "buy now or
wait" recommendation, and alerts you on Telegram and email the moment a
price hits your target - with a direct booking link included.

Everything runs on free tiers: GitHub Actions for scheduling and compute,
GitHub Pages for hosting, Cloudflare Workers for the secure bridge, and
Telegram/Gmail for notifications. No servers to maintain, no ongoing cost.

---

## What it does

- **Checks prices every 3 hours** via the Travelpayouts Data API, for as
  many routes as you want to track at once.
- **Checks alternative nearby airports** too, if you configure them (e.g.
  a Guangzhou route can also check Shenzhen, Hong Kong, and Macau) and
  automatically uses whichever is cheapest.
- **Shows price trend direction** (rising/falling/stable) based on your
  own route's recent price history - not a prediction model, just an
  honest comparison of recent vs. older prices.
- **Rates each price** as a Great/Good/Fair deal by comparing it to that
  route's own last 30 days of checks.
- **Gives a Buy-or-Wait recommendation** - a simple rule-based nudge
  combining the trend and deal-quality signals.
- **Sends a price trend chart image** alongside alerts once there's a
  little history built up.
- **Resolves airline codes to real names** (e.g. "MH" → "Malaysia
  Airlines") in every alert.
- **Includes a direct booking link** (Skyscanner Malaysia) in every alert,
  pre-filled with your route's dates.
- **Sends a weekly digest** summarizing every tracked route's current
  price and status, even on weeks nothing hits target.
- **Self-monitors** - if a route's price check fails 3 times in a row
  (e.g. an expired API token), you get a one-time warning instead of
  silently missing weeks of data.
- **Manage everything from your phone**: a Telegram Mini App lets you
  add, update, and remove routes with a native-feeling form, with a
  GitHub Issue Form as an equally capable fallback.
- **Or manage it from any browser**: a standalone, password-protected web
  page (`docs/web.html`) does the same add/update/remove job as the
  Telegram Mini App, for when you'd rather not go through Telegram at all.

---

## Architecture

```
Telegram Mini App    ──┐
Standalone Web Page  ──┼──> Cloudflare Worker ──> GitHub Issue ──> GitHub Actions workflow ──> config.yaml
GitHub Issue Form    ──┘                                                                            │
                                                                                                      v
                                                                GitHub Actions (every 3 hrs) ──> track_prices.py
                                                                                                      │
                                                                                                      v
                                                                            Telegram + Email (+ WhatsApp if enabled)
```

Three equally valid ways to add/update/remove a route:
1. **Telegram Mini App** - tap your bot's menu button, fill in a form.
2. **Standalone web page** (`docs/web.html`) - same form, in any browser,
   protected by a password instead of Telegram.
3. **GitHub Issue Form** - Issues → New Issue → "✈️ Add a flight route to track".

All three create a labeled GitHub Issue, which a workflow parses into
`config.yaml`. The price-checking pipeline doesn't care which one you used.
The Mini App and the web page share the same Cloudflare Worker - the only
difference is how each one proves it's really you (Telegram's signed
session data vs. a password you choose).

---

## Repository structure

```
├── track_prices.py              Main price-checking script (runs every 3 hrs)
├── config.yaml                  Tracked routes (edited via Mini App/Issue Form)
├── requirements.txt             Python dependencies
├── history.json                 Auto-generated price history (don't hand-edit)
│
├── scripts/
│   ├── parse_issue_route.py     Parses "Add Route" issues -> updates config.yaml
│   ├── remove_route.py          Parses "Remove Route" issues -> updates config.yaml
│   ├── notify_telegram.py       Sends route add/remove confirmations to Telegram
│   └── weekly_digest.py         Weekly summary script
│
├── docs/
│   ├── index.html               Telegram Mini App (hosted via GitHub Pages)
│   └── web.html                 Standalone password-protected web page,
│                                  same job as the Mini App, no Telegram needed
│
├── cloudflare-worker/
│   └── worker.js                Reference copy - the LIVE version lives in
│                                  Cloudflare's own dashboard, not GitHub
│
└── .github/
    ├── ISSUE_TEMPLATE/
    │   └── add_route.yml         "Add a flight route to track" form fields
    └── workflows/
        ├── check_prices.yml      Runs track_prices.py every 3 hours
        ├── add_route.yml         Triggers on "add-route" labeled issues
        ├── remove_route.yml      Triggers on "remove-route" labeled issues
        ├── weekly_digest.yml     Runs weekly_digest.py every Monday
        └── deploy-pages.yml      Redeploys the Mini App when docs/ changes
```

---

## One-time setup

### 1. Get a free Travelpayouts token
1. Sign up at https://www.travelpayouts.com (free, no payment info needed).
2. Go to your account's "API" / "Data API" section and copy your token.
3. *(Optional)* Your Partner ID / "marker" (shown on your dashboard) can be
   added later for affiliate tracking - not required for the tracker to work.

### 2. Set up Telegram alerts
1. Message **@BotFather** in Telegram, send `/newbot`, follow the prompts.
2. Save the **bot token** it gives you.
3. Send your new bot any message (e.g. "hi") so it can message you back -
   Telegram won't allow the bot to push messages until you do this once.
4. Get your chat ID: visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find
   `"chat":{"id":` in the response.

### 3. *(Optional)* Set up email backup
1. Turn on 2-Step Verification on your Gmail account:
   https://myaccount.google.com/security
2. Generate an App Password: https://myaccount.google.com/apppasswords

### 4. Create the GitHub repo
1. Create a **public** repo and upload all files from this project,
   preserving the folder structure exactly (including the hidden
   `.github` folder).
2. Go to **Settings → Secrets and variables → Actions** and add:

   | Secret | Required? | Value |
   |---|---|---|
   | `TRAVELPAYOUTS_TOKEN` | Yes | Your Travelpayouts token |
   | `TELEGRAM_BOT_TOKEN` | Yes | Token from @BotFather |
   | `TELEGRAM_CHAT_ID` | Yes | Your chat ID |
   | `TRAVELPAYOUTS_MARKER` | No | Your Partner ID, for affiliate tracking |
   | `GMAIL_USER` | No | Your Gmail address (email backup) |
   | `GMAIL_APP_PASSWORD` | No | 16-character app password |
   | `NOTIFY_EMAIL` | No | Where email alerts go |
   | `CALLMEBOT_PHONE` | No | WhatsApp via CallMeBot (see note below) |
   | `CALLMEBOT_APIKEY` | No | WhatsApp via CallMeBot (see note below) |

3. Create two labels under **Issues → Labels**: `add-route` and
   `remove-route`. This is required - without them, the Issue Form
   workflows won't trigger correctly.

### 5. *(Optional)* Set up the Telegram Mini App
This gives you an in-Telegram form instead of using GitHub's Issue Form.
It requires a few extra free pieces:

1. **Enable GitHub Pages**: Settings → Pages → Source: "Deploy from a
   branch" → Branch: `main`, folder `/docs`.
2. **Deploy the Cloudflare Worker**:
   - Sign up free at https://dash.cloudflare.com
   - Workers & Pages → Create → "Start with Hello World!"
   - Paste in the contents of `cloudflare-worker/worker.js`, click Deploy.
   - Under the Worker's **Settings → Variables and Secrets**, add:

     | Name | Type | Value |
     |---|---|---|
     | `BOT_TOKEN` | Secret | Your Telegram bot token |
     | `GITHUB_TOKEN` | Secret | A fine-grained GitHub PAT scoped to just this repo, with "Issues: write" permission |
     | `GITHUB_OWNER` | Text | Your GitHub username |
     | `GITHUB_REPO` | Text | Your repo name |
     | `ALLOWED_ORIGIN` | Text | Your GitHub Pages URL, e.g. `https://yourname.github.io` |

   - Under the Worker's **Settings → Domains & Routes**, make sure the
     **Production** toggle is switched **on** - this is easy to miss and
     the Worker won't respond publicly otherwise.
3. **Point the Mini App at your Worker**: edit `docs/index.html`, find the
   line `const WORKER_URL = "https://YOUR-WORKER-NAME...` and replace it
   with your actual Worker URL (ending in `/submit`).
4. **Register the Mini App with your bot**: message @BotFather →
   `/mybots` → your bot → Bot Settings → Menu Button → Edit Menu Button
   URL → paste your GitHub Pages URL.

**Note on WhatsApp (CallMeBot):** this is an optional, free, best-effort
secondary channel. It's a small hobby project with no uptime guarantee,
and its opt-in number changes periodically without notice - treat it as a
nice-to-have, not something to rely on. Telegram is the reliable primary
channel by design.

### 6. *(Optional)* Set up the standalone web page

Does the same job as the Telegram Mini App (add/update/remove routes) from
any browser, no Telegram required. It reuses the same GitHub Pages site and
Cloudflare Worker - if you already did step 5 above, there's very little
left to do:

1. **Re-deploy the Worker** with the latest `cloudflare-worker/worker.js`
   (it now handles both the Mini App and the web page - see the file's
   top comment for what changed). If you haven't set up the Worker at all
   yet, follow step 5's "Deploy the Cloudflare Worker" instructions first.
2. **Choose a password** and add it as one more Worker secret, under the
   same **Settings → Variables and Secrets** page as the others:

   | Name | Type | Value |
   |---|---|---|
   | `WEB_ADMIN_KEY` | Secret | A password you choose (treat it like any other password - the Worker checks it on every request) |

   Leaving this secret unset disables the web page's login entirely
   (Telegram-only requests keep working as before).
3. **GitHub Pages must already be enabled** (step 5.1) - `docs/web.html`
   is served from the same site as `docs/index.html`, no separate hosting
   needed.
4. **Point the page at your Worker**: edit `docs/web.html`, find the line
   `const WORKER_URL = "https://YOUR-WORKER-NAME...` and replace it with
   your actual Worker URL (ending in `/submit`) - the same URL you used
   for `docs/index.html`.
5. Visit `https://yourname.github.io/web.html`, enter your password once.
   The page remembers it in that browser for next time (via `localStorage`
   - it never leaves your device except as part of the login check itself).
   Use "Forget saved password" on the page to clear it from a shared or
   borrowed device.

**Security note:** unlike the Telegram Mini App - which is authenticated by
Telegram's own signed session data and can't be replayed by someone who
doesn't have your Telegram session - the web page relies on a single shared
password. That's an appropriate trade-off for a personal tool only you know
the URL to, but treat the password like you would any other login: don't
reuse one from elsewhere, and change it (update the `WEB_ADMIN_KEY` secret)
if you ever suspect it leaked.

---

## Using it day to day

### Add or update a route
Via Mini App (tap your bot's menu button), the standalone web page
(`docs/web.html`), or the GitHub Issue Form ("✈️ Add a flight route to
track"). Fill in:
- Route name (any label)
- Departure/destination IATA codes
- *(Optional)* Alternative airports, comma-separated (e.g. `SZX, HKG, MFM`)
- Departure/return dates (blank return date = one-way)
- Target price and currency

Submitting again with the **same origin+destination** updates that route
in place rather than duplicating it. Multiple different routes can be
tracked simultaneously.

### Remove a route
Via the "Remove" button on any tracked route in the Mini App or the
standalone web page, or by manually deleting its block from `config.yaml`.

### Check on things anytime
- **Actions tab → Flight Price Check → Run workflow**: trigger a check manually.
- **Actions tab → Weekly Digest → Run workflow**: get an on-demand summary
  of every route's current status, regardless of alert history.
- **`history.json`** in your repo: raw price log per route, if you want to
  inspect the data directly.

### Adjusting the check frequency
Edit the cron line in `.github/workflows/check_prices.yml` (currently
`0 */3 * * *`, every 3 hours). GitHub Actions cron syntax: `min hour day
month weekday`.

---

## Config reference (`config.yaml`)

```yaml
routes:
  - name: "Kuala Lumpur to Guangzhou"
    origin: KUL
    destination: CAN
    alt_destinations: [SZX, HKG, MFM]   # optional
    departure_date: "2026-09-16"
    return_date: "2026-09-20"            # blank "" for one-way
    target_price: 2000
    currency: myr
```

This file is normally managed automatically - hand-editing is only needed
for bulk changes or removing a route without the Mini App.

---

## Known limitations

- **Trend and Buy-or-Wait are heuristics, not predictions.** They compare
  your own route's recent price history - they're not a machine-learned
  forecast and can't know about future events (fare sales, seasonality
  shifts, etc.) that haven't happened yet in your data.
- **Deal quality and trend need a little history first** (3-4 checks
  minimum) before they show anything - this is by design, to avoid
  meaningless signals from a single data point.
- **The airline shown is sometimes a best-effort approximation**, flagged
  as such, when the primary price source doesn't include airline data and
  the closest available match isn't from the same date.
- **No Promotion Hunter yet.** Watching individual airline promo pages
  reliably requires either a stable RSS/deal-aggregator feed (not yet
  verified working) or fragile web scraping (avoided intentionally, since
  it breaks whenever a page redesigns and can brush up against some
  airlines' terms of service). If you find a real, stable feed URL, this
  can be added as a lightweight watcher.
- **WhatsApp via CallMeBot is unreliable by nature** - it's a free hobby
  project with no uptime guarantee. Telegram is the dependable channel.
- **Currency shown on the Skyscanner booking link may not always match**
  your route's tracked currency immediately - your Telegram/email alert
  always shows the true, correct price regardless of what the booking
  page defaults to on first load.

---

## Troubleshooting notes

- **Mini App shows "Failed to fetch"**: usually a stale cached page (fully
  close and reopen Telegram) or, on some Android devices, an
  incompatibility between the embedded WebView and Cloudflare's
  `workers.dev` domain that isn't fixable from the code side - the GitHub
  Issue Form works identically as a fallback.
- **"This job is skipped" on Actions**: check that the `add-route` /
  `remove-route` labels actually exist in your repo - GitHub only
  auto-applies a label if it already exists.
- **Git push conflicts between workflows**: all workflows that write to
  the repo share a `concurrency` group and pull the latest changes before
  committing, to avoid race conditions when multiple triggers fire close
  together.
- **Duplicate workflow runs for one issue**: caused by triggering on both
  `opened` and `labeled` when a label is attached at creation - our
  workflows intentionally only trigger on `opened`, `reopened`, and
  `edited`.
- **Web page says "Unauthorized" or keeps asking for the password**: check
  that `WEB_ADMIN_KEY` is actually set on the Worker (not just typed into
  the page) and that you re-deployed the latest `worker.js` after adding
  it. A stale cached password in the browser is cleared automatically on a
  401 - just re-enter it.

---

## Costs

Everything here is free at this scale:
- GitHub Actions: unlimited minutes on a public repo.
- GitHub Pages: free static hosting.
- Cloudflare Workers: generous free tier (100,000 requests/day).
- Travelpayouts Data API: free tier.
- Telegram Bot API: free, no usage limits for personal bots.
- Gmail SMTP: free, uses your own account.
