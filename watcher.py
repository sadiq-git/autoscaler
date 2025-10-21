import os,time,docker,pathlib
CONF_DIR=os.getenv('CONF_DIR','/work/conf.d'); APP_PORT=int(os.getenv('APP_PORT','8080'))
client=docker.from_env()

def list_backends():
    out=[]
    for c in client.containers.list():
        n=c.name
        if not (n=='app' or n.startswith('app-dup-')): continue
        nets=c.attrs.get('NetworkSettings',{}).get('Networks') or {}
        if not nets: continue
        ip=list(nets.values())[0].get('IPAddress')
        if ip: out.append(f'{ip}:{APP_PORT}')
    return sorted(out)

def write_conf(back):
    path=pathlib.Path(CONF_DIR)/'upstreams.conf'; path.parent.mkdir(parents=True,exist_ok=True)
    body='upstream app_pool {\n    keepalive 64;\n' + "\n".join([f'    server {b} max_fails=3 fail_timeout=10s;' for b in back]) + '\n}\n' \
         'server { listen 80; location / { proxy_pass http://app_pool; proxy_set_header Host $host; } }\n'
    old=path.read_text() if path.exists() else ''
    if body!=old: path.write_text(body); return True
    return False

def reload_nginx():
    try:
        exe=client.api.exec_create('lb', cmd=['nginx','-s','reload']); client.api.exec_start(exe['Id'])
    except Exception:
        try:
            exe=client.api.exec_create('lb', cmd=['sh','-lc','kill -HUP 1']); client.api.exec_start(exe['Id'])
        except Exception: pass

def main():
    last=[]
    while True:
        back=list_backends()
        if back!=last:
            if write_conf(back): reload_nginx()
            last=back
        time.sleep(3)
if __name__=='__main__': main()