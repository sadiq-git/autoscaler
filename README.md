# Agentic PoC v4.4 — Latency autoscaling with Gemini Planner
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
