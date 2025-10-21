import os, json, redis

def get_redis_client():
    host = os.getenv("REDIS_HOST","redis")
    port = int(os.getenv("REDIS_PORT","6379"))
    return redis.Redis(host=host, port=port, decode_responses=True)

def publish(channel: str, message: dict):
    r = get_redis_client()
    r.publish(channel, json.dumps(message))

def subscribe(channel: str):
    r = get_redis_client()
    p = r.pubsub()
    p.subscribe(channel)
    for msg in p.listen():
        if msg["type"] != "message": continue
        try:
            data = json.loads(msg["data"])
        except Exception:
            data = {"raw": msg["data"]}
        yield data

def safe_json(obj):
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)
