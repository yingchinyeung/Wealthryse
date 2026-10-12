"""Explicit one-process gateway entry point; never mount on the existing website."""
import json
from ibkr_etl.specialist_gateway_runtime import compose

if __name__=='__main__':
    import uvicorn
    try:
        with open('/app/gateway-build.json',encoding='utf-8') as f: manifest=json.load(f)
        app=compose(manifest['source_manifest_sha256'])
        uvicorn.run(app,host='0.0.0.0',port=8000,workers=1,access_log=False,limit_concurrency=8,
            timeout_keep_alive=5,proxy_headers=False)
    except Exception as error:
        print('SPECIALIST_GATEWAY_START_FAILED '+json.dumps({'error_type':type(error).__name__}),flush=True)
        raise SystemExit(1)
