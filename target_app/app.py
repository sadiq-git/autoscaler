# target_app/app.py
from flask import Flask, request
import os, time

app = Flask(__name__)

# env knob to slow responses (ms)
EXTRA_DELAY_MS = int(os.getenv("EXTRA_DELAY_MS", "0"))

@app.route("/", methods=["GET"])
def index():
    # allow override via query (?delay_ms=123), else use env
    delay_ms = int(request.args.get("delay_ms", EXTRA_DELAY_MS))
    if delay_ms > 0:
        time.sleep(delay_ms / 1000.0)
    return "OK\n"

@app.route("/work", methods=["GET"])
def work():
    ms = int(request.args.get("ms", "50"))
    # simulate slower handler
    time.sleep(ms / 1000.0)
    return f"work {ms}ms\n"

@app.route("/health", methods=["GET"])
def health():
    return "healthy\n", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
