export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Standard CORS headers so your website can fetch data without being blocked
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    // --- GET ROUTE: Frontend website fetches active player list ---
    if (request.method === "GET") {
      const playerData = await env.MC_STORAGE.get("players_json");
      return new Response(playerData || JSON.stringify({ count: 0, players: [] }), {
        headers: { "Content-Type": "application/json", ...corsHeaders }
      });
    }

    // --- POST ROUTE: Python watcher updates active player list ---
    if (request.method === "POST") {
      const authHeader = request.headers.get("Authorization");
      
      // Verify authorization token to prevent unauthorized tampering
      if (authHeader !== `Bearer ${env.API_SECRET}`) {
        return new Response(JSON.stringify({ error: "Unauthorized" }), { 
          status: 401, 
          headers: { "Content-Type": "application/json", ...corsHeaders } 
        });
      }

      try {
        const body = await request.text();
        // Save raw JSON directly to Cloudflare KV key
        await env.MC_STORAGE.put("players_json", body);
        return new Response(JSON.stringify({ success: true }), {
          headers: { "Content-Type": "application/json", ...corsHeaders }
        });
      } catch (err) {
        return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: corsHeaders });
      }
    }

    return new Response("Not Found", { status: 404 });
  }
};