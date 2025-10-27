# planner.py — v4.3 (throttle-safe, replica-aware)
import os, time, json, requests, traceback, random
from utils import subscribe, publish, safe_json

# === LLM wiring ===
LLM_URL = os.getenv(
    "LLM_URL",
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
HEADERS = {"Content-Type": "application/json", "x-goog-api-key": LLM_API_KEY}

# === Policy thresholds ===
UPSCALE_P95_MS   = float(os.getenv("UPSCALE_P95_MS", "300"))
DOWNSCALE_P95_MS = float(os.getenv("DOWNSCALE_P95_MS", "120"))
COOLDOWN_SEC     = float(os.getenv("COOLDOWN_SEC", "20"))

# === Throttling / cadence ===
LLM_RPM              = float(os.getenv("LLM_RPM", "10"))          # max LLM calls per minute
LLM_HEARTBEAT_SEC    = float(os.getenv("LLM_HEARTBEAT_SEC", "45"))# call at least this often if state unchanged
LLM_DEADBAND_MS      = float(os.getenv("LLM_DEADBAND_MS", "30"))  # band wiggle to avoid jitter calls
LLM_BACKOFF_BASE_SEC = float(os.getenv("LLM_BACKOFF_BASE_SEC", "5"))
LLM_BACKOFF_MAX_SEC  = float(os.getenv("LLM_BACKOFF_MAX_SEC", "60"))

# === Replica bounds ===
MIN_REPLICAS = int(os.getenv("MIN_REPLICAS", "1"))
MAX_REPLICAS = int(os.getenv("MAX_REPLICAS", "5"))

ALLOWED = {"noop", "restart", "scale_up", "scale_down"}

SYSTEM = f"""You are an autoscaling planner for a web service.
Return ONLY compact JSON:
{{"action":"noop|restart|scale_up|scale_down","target":"app","reason":"<short>"}}

Policy:
- If p95_ms > {UPSCALE_P95_MS} → "scale_up".
- If p95_ms < {DOWNSCALE_P95_MS} for 3 consecutive windows AND replicas > {MIN_REPLICAS} → "scale_down".
- Otherwise "noop".
Target is always "app". Keep reason short.
"""

_last_action_ts = 0.0
_low_windows    = []            # rolling booleans: p95 < DOWNSCALE_P95_MS
_last_state_key = None
_last_llm_call  = 0.0

# token bucket for LLM rate limit
_bucket_tokens  = LLM_RPM
_bucket_updated = time.time()

# exponential backoff for 429
_backoff_until  = 0.0
_backoff_power  = 0

def _cooldown_ok() -> bool:
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

def _take_token() -> bool:
    _refill_bucket()
    global _bucket_tokens
    if _bucket_tokens >= 1.0:
        _bucket_tokens -= 1.0
        return True
    return False

def _band(p95: float) -> str:
    if p95 > UPSCALE_P95_MS + LLM_DEADBAND_MS:
        return "band_high"
    if p95 < DOWNSCALE_P95_MS - LLM_DEADBAND_MS:
        return "band_low"
    return "band_mid"

def _should_call_llm(p95: float, replicas):
    """
    Decide if we should call the LLM this window.
    We trigger when the band/replica-state changes OR a jittered heartbeat elapses.
    """
    global _last_state_key, _last_llm_call
    b = _band(p95)
    # replicas may be None (unknown) — include as-is to reflect state changes.
    key = (b, replicas, tuple(_low_windows))
    now = time.time()
    changed = (key != _last_state_key)

    # add small jitter to spread calls
    hb = LLM_HEARTBEAT_SEC * (0.9 + 0.2 * random.random())
    heartbeat = (now - _last_llm_call) >= hb

    _last_state_key = key
    return changed or heartbeat

def _heuristic(event: dict) -> dict:
    """
    Cheap, reliable fallback policy (and primary when LLM is gated).
    Allows scale_down even if replicas is unknown; executor must enforce MIN_REPLICAS.
    """
    p95 = float(event.get("p95_ms", 0))
    replicas = event.get("replicas")  # may be None

    if p95 > UPSCALE_P95_MS:
        # Let executor cap at MAX_REPLICAS.
        return {
            "action": "scale_up",
            "target": "app",
            "reason": f"p95 {p95:.0f}ms > {UPSCALE_P95_MS:.0f}ms",
        }

    if all(_low_windows[-3:]):  # three consecutive low windows
        why = f"p95 < {DOWNSCALE_P95_MS:.0f}ms for 3 windows"
        if replicas is None:
            why += " (replicas_unknown; executor_enforces_min)"
        else:
            why += f" (replicas={replicas})"
        return {"action": "scale_down", "target": "app", "reason": why}

    return {"action": "noop", "target": "app", "reason": "heuristic"}

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

def _call_gemini(event, hints) -> dict:
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{
            "parts": [{
                "text": (
                    f"metrics: {safe_json(event)}\n"
                    f"hints: {safe_json(hints)}\n"
                    f"Return compact JSON only."
                )
            }]
        }],
        "generation_config": {
            "temperature": 0.05,
            "max_output_tokens": 128,
            "response_mime_type": "application/json",
        },
    }
    r = requests.post(LLM_URL, headers=HEADERS, json=body, timeout=20)
    if r.status_code == 429:
        _handle_429(r)
        raise RuntimeError("llm_429")
    r.raise_for_status()
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    parts = ((cand.get("content") or {}).get("parts") or [{}])
    txt = parts[0].get("text", "{}").strip()

    # handle accidental fenced JSON
    if txt.startswith("```"):
        txt = txt.strip("`").strip()
        if txt.lower().startswith("json"):
            txt = txt[4:].strip()

    data = json.loads(txt or "{}")
    a = data.get("action", "noop")
    if a not in ALLOWED:
        a = "noop"

    return {
        "action": a,
        "target": "app",
        "reason": str(data.get("reason", ""))[:160],
    }

def main():
    global _low_windows, _last_llm_call
    for msg in subscribe("alerts"):
        try:
            if msg.get("kind") != "latency_metrics":
                continue

            # metrics
            p95 = float(msg.get("p95_ms", 0))
            _low_windows.append(p95 < DOWNSCALE_P95_MS)
            _low_windows = _low_windows[-3:]

            # replicas may be included by monitor/watcher; else unknown
            replicas = msg.get("replicas")  # may be int or None

            hints = {
                "policy": {
                    "upscale_if_p95_gt": UPSCALE_P95_MS,
                    "downscale_if_p95_lt_3x": DOWNSCALE_P95_MS,
                    "min_replicas": MIN_REPLICAS,
                    "max_replicas": MAX_REPLICAS,
                },
                "cooldown_ok": _cooldown_ok(),
                "low_windows": _low_windows,
                "replicas": replicas,
            }

            # decide whether to call LLM this window
            call_llm = (
                LLM_API_KEY
                and time.time() >= _backoff_until
                and _should_call_llm(p95, replicas)
                and _cooldown_ok()
                and _take_token()
            )

            if call_llm:
                try:
                    decision = _call_gemini(
                        {"p95_ms": p95, "replicas": replicas}, hints
                    )
                    _last_llm_call = time.time()
                    _reset_backoff_ok()
                except Exception as e:
                    decision = _heuristic({"p95_ms": p95, "replicas": replicas})
                    decision["reason"] = f"{decision['reason']} (llm_fallback: {e})"
            else:
                decision = _heuristic({"p95_ms": p95, "replicas": replicas})
                if time.time() < _backoff_until:
                    decision["reason"] += " (llm_backoff)"
                elif not LLM_API_KEY:
                    decision["reason"] += " (no_llm_key)"
                elif not _cooldown_ok():
                    decision["reason"] += " (cooldown)"
                else:
                    decision["reason"] += " (cadence)"

            # enforce cooldown for impactful actions
            if (not _cooldown_ok()) and decision.get("action") in ("scale_up", "scale_down", "restart"):
                decision = {"action": "noop", "target": "app", "reason": "cooldown"}

            publish("actions", {
                "ts": time.time(),
                "kind": "plan",
                "container": "app",
                "decision": decision,
                "telemetry": {"p95_ms": p95, "replicas": replicas},
            })

            if decision.get("action") in ("scale_up", "scale_down", "restart"):
                _record_action()

        except Exception as e:
            traceback.print_exc()
            publish("actions", {"kind": "error", "error": str(e), "raw": msg})

if __name__ == "__main__":
    main()
