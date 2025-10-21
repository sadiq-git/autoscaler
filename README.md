# Agentic PoC v4.3 — Latency autoscaling with Gemini Planner
#
# What it does
# - monitor.py probes the LB (http://lb/) each window, computes avg & p95 latency
#   and publishes events: {"kind":"latency_metrics", ...}
# - planner.py sends these metrics to **Gemini** and gets a JSON decision:
#   {"action":"scale_up|scale_down|restart|noop", "target":"app", "reason":"..."}
# - executor.py applies the action (clone or remove app replicas, with a MAX cap)
# - watcher.py regenerates NGINX upstreams as replicas change, hot-reloads LB
# - dashboard.py shows live latency & recent actions on http://localhost:8090
# - app runs with Gunicorn behind NGINX; load hits http://localhost:8081/
#
# Prereqs
# - Docker + Docker Compose
# - Windows Docker Desktop users: Settings → General → “Expose daemon on
#   tcp://localhost:2375 (without TLS)” (needed for monitor/executor/watcher to
#   talk to Docker if you use the windows override compose file).
#
# Quick start (Windows)
#   cp .env.example .env
#   # put your Gemini key into .env
#   docker compose -f docker-compose.yml -f docker-compose.windows.yml.example up -d --build
#   # push traffic to see scaling
#   docker run -d --rm --name rps01 --network agentic_poc_gemini_local_v4_3_ai_default \
#     alpine:3.20 sh -c 'apk add --no-cache curl >/dev/null 2>&1; while true; do for i in $(seq 1 1200); do curl -s http://lb/ >/dev/null & done; wait; done'
#
# Quick start (Linux)
#   cp .env.example .env
#   docker compose up -d --build
#
# Open:
# - LB proxied app:   http://localhost:8081/
# - Dashboard (live): http://localhost:8090
#
# Tune in .env:
# - UPSCALE_P95_MS / DOWNSCALE_P95_MS  (planner policy)
# - COOLDOWN_SEC                       (anti-flap)
# - MAX_REPLICAS                       (safety cap)
# - SAMPLE_INTERVAL / PROBE_REQUESTS / TIMEOUT_S (monitor behavior)
#
# Logs to watch
#   docker compose logs -f monitor
#   docker logs -f $(docker ps --filter "name=subscriber" -q)
#
# Cleanup
#   docker compose down -v
#
# Notes
# - This version **calls Gemini** on every metrics window, so you’ll see usage
#   in your Google AI Studio / Gemini dashboard.
# - The planner validates and clamps responses to allowed actions.
#

