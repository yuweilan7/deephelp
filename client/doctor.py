import concurrent.futures,json,socket
from settings import BASE,settings
d=settings()
def check(port):
    try:
        with socket.create_connection((d['DEEPHELP_SSH_HOST'],port),timeout=5) as s:
            s.settimeout(5)
            if port in [9091,19530]:s.sendall(b'GET /healthz HTTP/1.1\r\nHost: probe\r\nConnection: close\r\n\r\n')
            if port==6379:s.sendall(b'*1\r\n$4\r\nPING\r\n')
            data=s.recv(128)
            return port,{'tcp_connect_returned':True,'response':data.decode(errors='replace'),'outcome':'protocol_response' if data else 'closed_without_protocol_response'}
    except OSError as e:return port,{'outcome':type(e).__name__}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:r=dict(pool.map(check,[22,3306,6379,19530,9091,48231]))
result={'from':'Windows client','host':d['DEEPHELP_SSH_HOST'],'probe':r,'note':'TCP connect can return before remote reachability is established on this client path, including unused control port 48231. Assess protocol response together with cloud firewall and server bind evidence.'}
assert r[22].get('response','').startswith('SSH-') and all(r[p]['outcome']!='protocol_response' for p in [3306,6379,19530,9091]),result
(BASE/'reports/public-port-check.json').write_text(json.dumps(result,indent=2))
print('PUBLIC_ACCESS_CHECK PASS: SSH banner received; database/WebUI connections closed or timed out without protocol response.')
