/**
 * Cloudflare Worker - bridges the Telegram Mini App to your GitHub repo.
 *
 * Flow:
 *   1. Mini App (docs/index.html) POSTs form data + Telegram initData here.
 *   2. This Worker verifies initData really came from Telegram (HMAC-SHA256
 *      signature check using your bot token) - this stops anyone else from
 *      forging a request even if they find this Worker's URL.
 *   3. If valid, creates a GitHub Issue in your repo using the same
 *      "### Label\n\nValue" format your existing add_route.yml workflow
 *      already knows how to parse, with the "add-route" label attached.
 *   4. Your existing GitHub Actions pipeline takes it from there, unchanged.
 *
 * Required Worker secrets/variables (set in Cloudflare dashboard):
 *   BOT_TOKEN      (secret) - your Telegram bot token from @BotFather
 *   GITHUB_TOKEN   (secret) - a fine-grained GitHub PAT scoped to just this
 *                             repo, with "Issues: write" permission
 *   GITHUB_OWNER   (var)    - your GitHub username, e.g. "ckm1268-cell"
 *   GITHUB_REPO    (var)    - your repo name, e.g. "FlightsPricingTracker"
 *   ALLOWED_ORIGIN (var)    - your GitHub Pages URL, e.g.
 *                             "https://ckm1268-cell.github.io"
 */

const FIELD_LABELS = {
  route_name: "Route name",
  origin: "Departure city (IATA code)",
  destination: "Destination city (IATA code)",
  departure_date: "Departure date",
  return_date: "Return date (leave blank for one-way)",
  target_price: "Target price",
  currency: "Currency",
};

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

async function hmacSha256Raw(keyBytes, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return new Uint8Array(sig);
}

async function hmacSha256Hex(keyBytes, message) {
  const raw = await hmacSha256Raw(keyBytes, message);
  return Array.from(raw).map((b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Validates Telegram Mini App initData per Telegram's documented algorithm:
 * https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
 */
async function validateInitData(initData, botToken, maxAgeSeconds = 86400) {
  const params = new URLSearchParams(initData);
  const receivedHash = params.get("hash");
  if (!receivedHash) return { valid: false, reason: "missing hash" };

  const authDate = parseInt(params.get("auth_date") || "0", 10);
  if (!authDate) return { valid: false, reason: "missing auth_date" };
  const ageSeconds = Math.floor(Date.now() / 1000) - authDate;
  if (ageSeconds > maxAgeSeconds) return { valid: false, reason: "initData expired" };

  const pairs = [];
  for (const [key, value] of params.entries()) {
    if (key === "hash") continue;
    pairs.push(`${key}=${value}`);
  }
  pairs.sort();
  const dataCheckString = pairs.join("\n");

  const secretKey = await hmacSha256Raw(new TextEncoder().encode("WebAppData"), botToken);
  const computedHash = await hmacSha256Hex(secretKey, dataCheckString);

  if (computedHash !== receivedHash) return { valid: false, reason: "hash mismatch" };

  let user = null;
  try {
    user = JSON.parse(params.get("user") || "null");
  } catch (_) {}

  return { valid: true, user };
}

function buildIssueBody(fields) {
  return Object.entries(FIELD_LABELS)
    .map(([key, label]) => `### ${label}\n\n${fields[key] || "_No response_"}`)
    .join("\n\n");
}

async function createGithubIssue(env, fields) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/issues`;
  const body = {
    title: `Add route: ${fields.route_name}`,
    body: buildIssueBody(fields),
    labels: ["add-route"],
  };

  const resp = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "flight-tracker-mini-app-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`GitHub API error (${resp.status}): ${text.slice(0, 300)}`);
  }
  return await resp.json();
}

function validateFields(fields) {
  const required = ["route_name", "origin", "destination", "departure_date", "target_price", "currency"];
  for (const key of required) {
    if (!fields[key] || String(fields[key]).trim() === "") {
      return `Missing required field: ${key}`;
    }
  }
  if (fields.origin.length !== 3 || fields.destination.length !== 3) {
    return "IATA codes must be exactly 3 letters";
  }
  if (isNaN(Number(fields.target_price)) || Number(fields.target_price) <= 0) {
    return "Target price must be a positive number";
  }
  return null;
}

export default {
  async fetch(request, env) {
    const headers = corsHeaders(env);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/submit" || request.method !== "POST") {
      return new Response(JSON.stringify({ ok: false, error: "Not found" }), {
        status: 404,
        headers: { ...headers, "Content-Type": "application/json" },
      });
    }

    let payload;
    try {
      payload = await request.json();
    } catch (_) {
      return new Response(JSON.stringify({ ok: false, error: "Invalid JSON body" }), {
        status: 400,
        headers: { ...headers, "Content-Type": "application/json" },
      });
    }

    const { initData, ...fields } = payload;

    if (!initData) {
      return new Response(JSON.stringify({ ok: false, error: "Missing initData" }), {
        status: 400,
        headers: { ...headers, "Content-Type": "application/json" },
      });
    }

    const check = await validateInitData(initData, env.BOT_TOKEN);
    if (!check.valid) {
      return new Response(JSON.stringify({ ok: false, error: "Unauthorized: " + check.reason }), {
        status: 401,
        headers: { ...headers, "Content-Type": "application/json" },
      });
    }

    const fieldError = validateFields(fields);
    if (fieldError) {
      return new Response(JSON.stringify({ ok: false, error: fieldError }), {
        status: 400,
        headers: { ...headers, "Content-Type": "application/json" },
      });
    }

    try {
      const issue = await createGithubIssue(env, fields);
      return new Response(JSON.stringify({ ok: true, issue_number: issue.number }), {
        status: 200,
        headers: { ...headers, "Content-Type": "application/json" },
      });
    } catch (err) {
      return new Response(JSON.stringify({ ok: false, error: err.message }), {
        status: 500,
        headers: { ...headers, "Content-Type": "application/json" },
      });
    }
  },
};
