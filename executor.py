import time, re, docker, traceback, os
from utils import subscribe, publish
client = docker.from_env()
MAX_REPLICAS = int(os.getenv("MAX_REPLICAS","5"))  # total (base + dups)

def sibling_name(base): return f"{base}-dup-{int(time.time())}"

def list_all_like(base):
    out=[]
    for c in client.containers.list(all=True):
        if c.name==base or re.match(rf"^{re.escape(base)}-dup-\d+$", c.name):
            out.append(c)
    return sorted(out, key=lambda c: c.name)

def list_siblings(base): return [c for c in list_all_like(base) if c.name!=base]

def clone_like(target_name):
    all_like = list_all_like(target_name)
    if len(all_like) >= MAX_REPLICAS:
        return {"status":"noop","message":f"max replicas {MAX_REPLICAS} reached"}
    base=client.containers.get(target_name)
    image=base.image.tags[0] if base.image.tags else base.image.id
    nets=list((base.attrs.get("NetworkSettings",{}).get("Networks") or {}).keys())
    kwargs=dict(name=sibling_name(target_name), image=image, detach=True)
    if nets: kwargs["network"]=nets[0]
    c=client.containers.run(**kwargs)
    return {"status":"ok","message":f"started {c.name} from {image}"}

def scale_up(target): return clone_like(target)

def scale_down(target):
    sibs=list_siblings(target)
    if not sibs: return {"status":"noop","message":"no siblings to remove"}
    v=sibs[-1]; v.stop(timeout=5); v.remove(); return {"status":"ok","message":f"removed {v.name}"}

def do_restart(name):
    c=client.containers.get(name); c.restart(timeout=5); return {"status":"ok","message":f"restarted {name}"}

def main():
    for msg in subscribe("actions"):
        try:
            if msg.get("kind") != "plan": continue
            a=msg["decision"]["action"]; t=msg["decision"]["target"]; r=msg["decision"]["reason"]
            try:
                tc=client.containers.get(t); labels=tc.labels or {}
                if labels.get("agentic.target")!="true":
                    publish("results", {"ts":time.time(),"action":"noop","target":t,"reason":"target not labeled agentic.target=true","result":{"status":"skipped"}}); continue
            except Exception:
                publish("results", {"ts":time.time(),"action":"noop","target":t,"reason":"target container not found","result":{"status":"error"}}); continue
            if a=="restart": res=do_restart(t)
            elif a=="scale_up": res=scale_up(t)
            elif a=="scale_down": res=scale_down(t)
            else: res={"status":"ok","message":"noop"}
            publish("results", {"ts":time.time(),"action":a,"target":t,"reason":r,"result":res})
        except Exception as e:
            traceback.print_exc(); publish("results", {"status":"error","error":str(e),"raw":msg})

if __name__ == "__main__":
    main()