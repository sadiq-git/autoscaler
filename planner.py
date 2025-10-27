# planner.py — v4.4 dynamic (baseline-aware, throttle-safe, replica-aware)
import os, time, json, random, traceback, statistics
from collections import deque
import requests
from utils import subscribe, publish, safe_json

# --- LLM config ---
LLM_URL = os.getenv("LLM_URL","https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
LLM_API_KEY = os.getenv("LLM_API_KEY","")
HEADERS = {"Content-Type":"application/json","x-goog-api-key": LLM_API_KEY}

# --- Scaling bounds & cooldown ---
COOLDOWN_SEC = float(os.getenv("COOLDOWN_SEC","20"))
MIN_REPLICAS = int(os.getenv("MIN_REPLICAS","2"))   # keep at least two for your demo
MAX_REPLICAS = int(os.getenv("MAX_REPLICAS","10"))

# --- Throttling / cadence ---
LLM_RPM              = float(os.getenv("LLM_RPM","2"))       # calls/min
LLM_HEARTBEAT_SEC    = float(os.getenv("LLM_HEARTBEAT_SEC","300"))
LLM_BACKOFF_BASE_SEC = float(os.getenv("LLM_BACKOFF_BASE_SEC","10"))
LLM_BACKOFF_MAX_SEC  = float(os.getenv("LLM_BACKOFF_MAX_SEC","300"))

# --- Learning / sensitivity (all dynamic; no hard-coded ms thresholds) ---
HIST_WINDOWS   = int(os.getenv("HIST_WINDOWS","60"))  # p95 windows tracked
WARMUP_WINDOWS = int(os.getenv("WARMUP_WINDOWS","12"))# need this many before trusting baseline
LOW_NEED_N     = int(os.getenv("LOW_NEED_N","3"))     # consecutive "near baseline" windows to downscale
ALPHA_UP       = float(os.getenv("ALPHA_UP","8.0"))   # scale_up if p95 >= ALPHA_UP * baseline (guardrail)
BETA_DOWN      = float(os.getenv("BETA_DOWN","1.10")) # "near baseline" if p95 <= BETA_DOWN * baseline
K_SIGMA        = float(os.getenv("K_SIGMA","2.5"))    # extra guard using sigma (z-score-ish)
IDLE_HINT_MS   = float(os.getenv("IDLE_HINT_MS","0")) # optional seed until baseline warms up (0=off)

ALLOWED = {"noop","restart","scale_up","scale_down"}

SYSTEM = f"""You are an autoscaling planner for a web service.
You receive recent latency history (p95 per window), a rolling baseline (median of p95),
dispersion sigma (1.4826 * MAD), current p95, recent 'near-baseline' booleans, replica counts,
and cooldown status.

Your job: decide "scale_up", "scale_down", or "noop" for target "app" using only data-driven rules.
Return ONLY this compact JSON:
{{"action":"noop|restart|scale_up|scale_down","target":"app","reason":"<short>"}}

Principles:
- Treat baseline_ms ≈ idle latency (robust rolling median of p95).
- Define pct_of_baseline = p95_ms / baseline_ms. Also consider sigma_ms.
- High load ⇒ if pct_of_baseline is clearly elevated (≈ ≥ 6–12×) OR (p95_ms - baseline_ms)/max(sigma_ms,1) is very high (≈ ≥ 6),
  and replicas < max_replicas, choose "scale_up".
- Idle/low ⇒ if pct_of_baseline ≤ ~1.05–1.30 for several consecutive windows (≥ 3) AND replicas > min_replicas, choose "scale_down".
- Always respect cooldown: if cooldown_ok is false, return "noop" with reason "cooldown".
- Keep reasons short (e.g., "8.3x baseline", "near baseline for 3w").

Target is always "app".
"""

# --- State ---
_last_action_ts = 0.0
_last_llm_call  = 0.0
_bucket_tokens  = LLM_RPM
_bucket_updated = time.time()
_backoff_until  = 0.0
_backoff_power  = 0

p95_hist = deque(maxlen=HIST_WINDOWS)
low_flags = deque(maxlen=LOW_NEED_N)
_last_state_key = None

def _cooldown_ok():
    return (time.time() - _last_action_ts) >= COOLDOWN_SEC

def _record_action():
    global _last_action_ts
    _last_action_ts = time.time()

def _refill_bucket():
    global _bucket_tokens, _bucket_updated
    now = time.time()
    delta_min = (now - _bucket_updated) / 60.0
    _bucket_tokens = min(LLM_RPM, _bucket_tokens + LLM_RPM * delta_min)
    _bucket_updated = now

def _take_token():
    _refill_bucket()
    global _bucket_tokens
    if _bucket_tokens >= 1.0:
        _bucket_tokens -= 1.0
        return True
    return False

def _robust_stats(samples):
    if not samples:
        return 0.0, 0.0
    med = statistics.median(samples)
    if len(samples) == 1:
        return med, 0.0
    abs_dev = [abs(x - med) for x in samples]
    mad = statistics.median(abs_dev)
    sigma = 1.4826 * mad if mad > 0 else 0.0
    return med, sigma

def _is_near_baseline(p95, baseline, sigma):
    """Counts as 'near baseline' if within BETA_DOWN × baseline, with small sigma cushion."""
    if baseline <= 0:
        return False
    # Allow a tiny additive cushion for noisy baselines
    cushion = max(5.0, 0.25 * sigma)
    return p95 <= (baseline * BETA_DOWN + cushion)

def _band_key(baseline, sigma, p95, replicas):
    if baseline <= 0:
        return ("init", replicas)
    ratio = p95 / baseline if baseline > 0 else 0.0
    if   ratio >= 8:  band = "very_high"
    elif ratio >= 3:  band = "high"
    elif ratio >= 1.5:band = "mid"
    elif ratio >= 0.9:band = "near"
    else:             band = "low"
    return (band, replicas, tuple(low_flags))

def _handle_429(resp):
    global _backoff_until, _backoff_power
    ra = resp.headers.get("Retry-After")
    try:
        wait = float(ra) if ra else (LLM_BACKOFF_BASE_SEC * (2 ** _backoff_power))
    except Exception:
        wait = LLM_BACKOFF_BASE_SEC * (2 ** _backoff_power)
    wait = min(wait, LLM_BACKOFF_MAX_SEC)
    _backoff_power = min(_backoff_power + 1, 4)
    _backoff_until = time.time() + wait

def _reset_backoff_ok():
    global _backoff_power
    _backoff_power = 0

def _call_gemini(payload):
    body = {
        "system_instruction":{"parts":[{"text": SYSTEM}]},
        "contents":[{"parts":[{"text": safe_json(payload)}]}],
        "generation_config":{
            "temperature":0.05,
            "max_output_tokens":128,
            "response_mime_type":"application/json"
        }
    }
    r = requests.post(LLM_URL, headers=HEADERS, json=body, timeout=20)
    if r.status_code == 429:
        _handle_429(r)
        raise RuntimeError("llm_429")
    r.raise_for_status()
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    parts = ((cand.get("content") or {}).get("parts") or [{}])
    txt = parts[0].get("text","{}").strip()
    if txt.startswith("```"):
        txt = txt.strip("`").strip()
        if txt.lower().startswith("json"):
            txt = txt[4:].strip()
    data = json.loads(txt)
    a = data.get("action","noop")
    if a not in ALLOWED:
        a = "noop"
    reason = str(data.get("reason","")).strip()[:160]
    return {"action": a, "target":"app", "reason": reason}

def _heuristic_decision(p95, baseline, sigma, replicas, have_baseline):
    """Conservative fallback when LLM is backing off/unavailable."""
    if not have_baseline:
        return {"action":"noop","target":"app","reason":"warming"}
    ratio = (p95 / baseline) if baseline > 0 else 0.0
    # Upscale: large ratio or strong sigma breach
    z = ((p95 - baseline) / (sigma if sigma > 0 else 1.0))
    if (ratio >= ALPHA_UP or z >= 6.0) and replicas < MAX_REPLICAS:
        return {"action":"scale_up","target":"app","reason":f"{ratio:.1f}x baseline"}
    # Downscale: consecutive near-baseline windows
    if all(low_flags) and replicas > MIN_REPLICAS:
        return {"action":"scale_down","target":"app","reason":"near baseline for 3w"}
    return {"action":"noop","target":"app","reason":"heuristic"}

def main():
    global _last_llm_call, _last_state_key

    for msg in subscribe("alerts"):
        try:
            if msg.get("kind") != "latency_metrics":
                continue

            p95 = float(msg.get("p95_ms", 0))
            p95_hist.append(p95)

            # Learn baseline/sigma from rolling history
            baseline, sigma = _robust_stats(list(p95_hist))
            have_baseline = (len(p95_hist) >= max(1, WARMUP_WINDOWS))
            if not have_baseline and IDLE_HINT_MS > 0 and baseline == 0:
                baseline = IDLE_HINT_MS  # optional seed until warm

            replicas = int(msg.get("replicas", 1))

            # Track recent "near baseline" windows for downscale eligibility
            near = _is_near_baseline(p95, baseline, sigma) if have_baseline else False
            low_flags.append(bool(near))

            # Prepare payload for LLM to infer decisions from ratios/z
            payload = {
                "p95_ms": p95,
                "baseline_ms": baseline,
                "sigma_ms": sigma,
                "pct_of_baseline": (p95 / baseline) if baseline > 0 else None,
                "low_windows": list(low_flags),
                "replicas": replicas,
                "min_replicas": MIN_REPLICAS,
                "max_replicas": MAX_REPLICAS,
                "cooldown_ok": _cooldown_ok(),
                "have_baseline": have_baseline,
                "params": {
                    "ALPHA_UP": ALPHA_UP,
                    "BETA_DOWN": BETA_DOWN,
                    "K_SIGMA": K_SIGMA,
                    "LOW_NEED_N": LOW_NEED_N,
                    "WARMUP_WINDOWS": WARMUP_WINDOWS
                }
            }

            # Decide whether to call LLM (state change or heartbeat), also respect token-bucket and cooldown
            state_key = _band_key(baseline, sigma, p95, replicas)
            changed = (state_key != _last_state_key)
            _last_state_key = state_key

            jittered_hb = LLM_HEARTBEAT_SEC * (0.9 + 0.2 * random.random())
            heartbeat = (time.time() - _last_llm_call) >= jittered_hb

            call_llm = (
                LLM_API_KEY
                and time.time() >= _backoff_until
                and (changed or heartbeat)
                and _cooldown_ok()
                and _take_token()
            )

            if call_llm:
                try:
                    decision = _call_gemini(payload)
                    _last_llm_call = time.time()
                    _reset_backoff_ok()
                except Exception as e:
                    decision = _heuristic_decision(p95, baseline, sigma, replicas, have_baseline)
                    decision["reason"] = f"{decision['reason']} (llm_fallback: {e})"
            else:
                decision = _heuristic_decision(p95, baseline, sigma, replicas, have_baseline)
                if time.time() < _backoff_until:
                    decision["reason"] += " (llm_backoff)"
                elif not LLM_API_KEY:
                    decision["reason"] += " (no_llm_key)"
                elif not _cooldown_ok():
                    decision["reason"] += " (cooldown)"
                else:
                    decision["reason"] += " (cadence)"

            # Enforce cooldown for impactful actions
            if not _cooldown_ok() and decision.get("action") in ("scale_up","scale_down","restart"):
                decision = {"action":"noop","target":"app","reason":"cooldown"}

            publish("actions", {
                "ts": time.time(),
                "kind":"plan",
                "container":"app",
                "decision": decision,
                "telemetry": {
                    "p95_ms": p95,
                    "baseline_ms": baseline,
                    "sigma_ms": sigma,
                    "low_windows": list(low_flags),
                    "replicas": replicas
                }
            })

            if decision.get("action") in ("scale_up","scale_down","restart"):
                _record_action()

        except Exception as e:
            traceback.print_exc()
            publish("actions", {"kind":"error","error":str(e),"raw":msg})

if __name__ == "__main__":
    main()
