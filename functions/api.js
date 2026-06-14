/* ==========================================================================
   File: functions/api.js
   Date: 2026-06-13
   Author: Gemini
   Description: Cloudflare Pages Function to fetch live Edmonton transit data,
                maintain a rolling 20-record history, and store to Workers KV.
   ========================================================================== */

export async function onRequest(context) {
  // context.env.TRAFFIC_DATA is how we talk to your KV database
  const kv = context.env.TRAFFIC_DATA;

  if (!kv) {
    return new Response(JSON.stringify({ error: "KV database binding missing." }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  }

  try {
    // 1. Pull existing history from KV (default to empty array if none exists)
    let history = await kv.get("recent_bus_records", { type: "json" }) || [];

    // 2. Fetch the fresh real-time bus data from Edmonton open data
    const liveResponse = await fetch("YOUR_EDMONTON_GTFS_RT_API_URL");
    const freshData = await liveResponse.json();

    // Attach a timestamp to the new snapshot
    freshData.snapshot_time = new Date().toISOString();

    // 3. Add fresh data to the front of the array
    history.unshift(freshData);

    // 4. Cap the history length at exactly 20 records
    if (history.length > 20) {
      history = history.slice(0, 20);
    }

    // 5. Save the updated rolling list back to the KV database
    await kv.put("recent_bus_records", JSON.stringify(history));

    // 6. Return the combined data to your website frontend
    return new Response(JSON.stringify(history), {
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*"
      }
    });

  } catch (error) {
    return new Response(JSON.stringify({ error: error.message }), {
      status: 500,
      headers: { "Content-Type": "application/json" }
    });
  }
}
