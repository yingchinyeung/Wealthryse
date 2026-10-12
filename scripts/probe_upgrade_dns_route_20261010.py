"""Read-only DNS/TLS proof; no credentials, local DNS overrides or data writes."""
import ipaddress,json,socket,ssl,time


def main():
    results=[]
    for host,expected in EXPECTED.items():
        deadline=time.monotonic()+60
        while True:
            addresses=sorted({x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
            if MODE not in ('private','public'):raise ValueError('Unknown DNS acceptance mode')
            correct=addresses==[expected] if MODE=='private' else bool(addresses) and all(ipaddress.ip_address(x).is_global for x in addresses)
            if correct:break
            if time.monotonic()>=deadline:raise ValueError('Expected DNS route not observed for '+host)
            time.sleep(2)
        with socket.create_connection((host,443),timeout=20) as sock:
            with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as tls:
                peer=tls.getpeername()[0]
                if peer not in addresses or MODE=='private' and peer!=expected:raise ValueError('TLS peer differs from DNS route')
        results.append({'host':host,'addresses':addresses,'peer':peer,'verified_tls':True})
    print('UPGRADE_DNS_ROUTE '+json.dumps({'passed':True,'mode':MODE,'results':results,
        'dns_overridden':False,'credentials_read':False,'database_writes':0,'blob_writes':0}),flush=True)


if __name__=='__main__':main()
