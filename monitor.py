import os, time, math, requests
from utils import publish

# --- Probe config ---
SAMPLE_INTERVAL = float(os.getenv("SAMPLE_INTERVAL", "3"))
PROBE_REQUESTS  = int(os.getenv("PROBE_REQUESTS", "40"))
TARGET_URL      = os.getenv("TARGET_URL", "http://lb/")
TIMEOUT_S       = float(os.getenv("TIMEOUT_S", "2.5"))

# --- Docker (optional) to report replica count ---
# Works if /var/run/docker.sock is mounted. Falls back to 1 if not available.
def _count_replicas():
    try:
        import docker  # lazy import so container starts even if not installed
        client = docker.from_env()
        n = 0
        # primary 'app' and duplicates created by executor (prefix 'app-dup-')
        for c in client.containers.list(filters={"status": "running"}):
            name = c.name
            if name == "app" or name.startswith("app-dup-"):
                n += 1
        return max(n, 1)
    except Exception:
        return 1

def _p95(values):
    if not values:
        return 0.0
    vs = sorted(values)
    k = 0.95 * (len(vs) - 1)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return vs[lo]
    return vs[lo] + (k - lo) * (vs[hi] - vs[lo])

def _probe_once(sess):
    t0 = time.perf_counter()
    ok = False
    try:
        r = sess.get(TARGET_URL, timeout=TIMEOUT_S)
        ok = (200 <= r.status_code < 300)
    except requests.RequestException:
        ok = False
    dt_ms = (time.perf_counter() - t0) * 1000.0
    return ok, dt_ms

def main():
    sess = requests.Session()
    while True:
        lat = []
        ok = 0
        for _ in range(PROBE_REQUESTS):
            success, ms = _probe_once(sess)
            ok += 1 if success else 0
            lat.append(ms)

        avg_ms = (sum(lat) / len(lat)) if lat else 0.0
        p95_ms = _p95(lat)
        success_rate = ok / (len(lat) or 1)

        evt = {
            "kind": "latency_metrics",
            "endpoint": TARGET_URL,
            "window_sec": SAMPLE_INTERVAL,
            "requests": len(lat),
            "success_rate": round(success_rate, 3),
            "avg_ms": round(avg_ms, 1),
            "p95_ms": round(p95_ms, 1),
            "replicas": _count_replicas(),  # <--- NEW: real replica count
            "ts": time.time(),
        }
        print("[monitor] latency_metrics:", evt, flush=True)
        publish("alerts", evt)
        time.sleep(SAMPLE_INTERVAL)

if __name__ == "__main__":
    main()
