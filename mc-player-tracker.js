// NOTE ON SECRETS:
//   env.API_SECRET          -> same bearer token the plugin already uses (wrangler secret put API_SECRET)
//   env.DISCORD_STATUS_WEBHOOK -> the PUBLIC status webhook URL (wrangler secret put DISCORD_STATUS_WEBHOOK)
// Don't hardcode these here — set them with `wrangler secret put <NAME>` so they
// aren't committed to the repo (this also covers the credential-rotation task
// that's already on the list).

const STATUS_PING = "<@&1306303285401223188>";

// If we haven't heard a heartbeat in this long, and it wasn't a graceful
// shutdown, assume the server crashed or lost power.
const STALE_THRESHOLD_SECONDS = 245; // 4 min = ~4 missed 60s heartbeats

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    // --- GET: Frontend website fetches active player list ---
    if (request.method === "GET" && url.pathname === "/") {
      const playerData = await env.MC_STORAGE.get("players_json");
      return new Response(playerData || JSON.stringify({ count: 0, players: [] }), {
        headers: { "Content-Type": "application/json", ...corsHeaders }
      });
    }

    // --- POST /: Python plugin syncs the active player list + heartbeat ---
    if (request.method === "POST" && url.pathname === "/") {
      const authHeader = request.headers.get("Authorization");
      if (authHeader !== `Bearer ${env.API_SECRET}`) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), {
          status: 401,
          headers: { "Content-Type": "application/json", ...corsHeaders }
        });
      }

      try {
        const body = await request.text();
        await env.MC_STORAGE.put("players_json", body);

        // A fresh heartbeat proves the server is alive right now, so clear
        // any stale flags from a previous outage/shutdown.
        await env.MC_STORAGE.put("crash_alerted", "false");
        await env.MC_STORAGE.put("graceful_shutdown", "false");

        return new Response(JSON.stringify({ success: true }), {
          headers: { "Content-Type": "application/json", ...corsHeaders }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: corsHeaders });
      }
    }

    // --- POST /status: plugin tells us about a clean startup/shutdown ---
    if (request.method === "POST" && url.pathname === "/status") {
      const authHeader = request.headers.get("Authorization");
      if (authHeader !== `Bearer ${env.API_SECRET}`) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), {
          status: 401,
          headers: { "Content-Type": "application/json", ...corsHeaders }
        });
      }

      try {
        const body = await request.json();
        if (body.event === "shutdown") {
          // Graceful stop — the plugin already sent its own "OFFLINE" ping,
          // so tell the cron job not to also raise a crash alert.
          await env.MC_STORAGE.put("graceful_shutdown", "true");
        } else if (body.event === "startup") {
          await env.MC_STORAGE.put("graceful_shutdown", "false");
          await env.MC_STORAGE.put("crash_alerted", "false");
        }
        return new Response(JSON.stringify({ success: true }), {
          headers: { "Content-Type": "application/json", ...corsHeaders }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: corsHeaders });
      }
    }

    return new Response("Not Found", { status: 404 });
  },

  // Runs on the schedule defined in wrangler.toml ([triggers] crons = [...]).
  // This is what lets us detect a crash/power-outage even though nothing on
  // your PC is left running to send that alert itself.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(checkHeartbeat(env));
  },
};

async function checkHeartbeat(env) {
  const raw = await env.MC_STORAGE.get("players_json");
  if (!raw) return; // no data synced yet, nothing to check

  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    return;
  }

  const updatedAt = data.updated_at || 0;
  const now = Math.floor(Date.now() / 1000);

  if (now - updatedAt <= STALE_THRESHOLD_SECONDS) return; // still healthy

  const graceful = (await env.MC_STORAGE.get("graceful_shutdown")) === "true";
  if (graceful) return; // expected shutdown, plugin already posted its own message

  const alreadyAlerted = (await env.MC_STORAGE.get("crash_alerted")) === "true";
  if (alreadyAlerted) return; // don't spam repeated alerts for the same outage

  const minutesSilent = Math.round((now - updatedAt) / 60);
  const message = {
    content:
      `🔴 **Server Update:** ${STATUS_PING} Synthesizer has gone silent for ~${minutesSilent} minutes ` +
      `with no shutdown notice — possible crash or power outage.`,
  };

  try {
    await fetch(env.DISCORD_STATUS_WEBHOOK, {
      method: "POST",
      headers: { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0" },
      body: JSON.stringify(message),
    });
    await env.MC_STORAGE.put("crash_alerted", "true");
  } catch (err) {
    // If this fetch fails, we simply try again on the next cron tick since
    // crash_alerted was never set to "true".
    console.error("Failed to send crash alert webhook:", err);
  }
}