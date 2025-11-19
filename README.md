# Agentic Autoscaler POC (Gemini)

This repository contains a proof-of-concept autoscaler that uses runtime latency telemetry and an LLM (Google Gemini) as an *optional* planner to make scaling decisions. It demonstrates a hybrid approach: heuristic safety nets + optional AI-driven dynamic thresholds.

## What it includes
- `monitor/` — simple latency probe that samples the LB and publishes p95 windows.
- `planner.py` — **Planner** (Gemini-enabled). Learns a rolling baseline and asks the LLM (optionally) whether to scale up/down. Includes conservative heuristics as fallback.
- `executor/` — starts/stops container replicas (simple docker-based executor).
- `watcher/` — updates nginx upstreams when replicas change.
- `subscriber/` — displays results and applies actions (simulated).
- `app/` — toy Flask app used for load testing.
- `nginx/` — LB config that proxies to `app` replicas.
- `dashboard/` — lightweight UI showing p95 and recent actions.

## How it works (high level)
1. **Monitor** probes the LB every `SAMPLE_INTERVAL` seconds and computes `p95_ms`, `avg_ms`, success rate, then publishes `alerts` to Redis.
2. **Planner** subscribes to `alerts` and keeps a rolling history of p95 windows. It computes a robust baseline (median) and dispersion (MAD→sigma).  
   - If an LLM key is present, the planner may call Gemini with the telemetry/hints and receive a compact JSON decision (`scale_up/scale_down/noop`).
   - If the LLM is unavailable or rate-limited, a conservative heuristic decides.
   - Planner respects `MIN_REPLICAS`, `MAX_REPLICAS`, and `COOLDOWN_SEC`.
3. **Executor** subscribes to `actions` and starts/stops app replicas (creates `app-dup-*` containers).  
4. **Watcher** rewrites nginx upstreams so LB forwards traffic to all healthy replicas.
5. **Subscriber/Dashboard** show the action results and telemetry.

## What role does the AI play?
- The LLM (Gemini) is used as an **advisor** to derive dynamic thresholds from recent telemetry rather than relying on fixed ms cutoffs.
- It receives: current p95, baseline, sigma, replica count, cooldown status, and recent low-windows — and returns a compact action JSON.
- There are safety guards: token-bucket rate limiting (`LLM_RPM`), backoff on 429, and heuristic fallback to guarantee safe behavior when the LLM is unavailable.

## Key configuration (example `.env`)
