from flask import Flask, jsonify, send_from_directory
import threading
from utils import subscribe

app = Flask(__name__)
state = {"latency": {}, "events": []}

def listen_alerts():
    for msg in subscribe("alerts"):
        if msg.get("kind") == "latency_metrics":
            state["latency"] = {
                "endpoint": msg.get("endpoint"),
                "avg_ms": msg.get("avg_ms",0),
                "p95_ms": msg.get("p95_ms",0),
                "success_rate": msg.get("success_rate",1.0),
                "window_sec": msg.get("window_sec",0)
            }

def listen_results():
    for msg in subscribe("results"):
        state["events"].append(msg); state["events"]=state["events"][-100:]

@app.get("/api/state")
def api_state():
    return jsonify(state)

@app.get("/")
def ui():
    return send_from_directory(".", "ui.html")

def main():
    threading.Thread(target=listen_alerts, daemon=True).start()
    threading.Thread(target=listen_results, daemon=True).start()
    app.run(host="0.0.0.0", port=8090)

if __name__ == "__main__":
    main()
