"""Read a bounded, exact-execution diagnostic tail; no dispatch or mutations."""
import json
import sys
from datetime import datetime,timezone
import build_ms_presentation_worker_candidate as build
import release_ms_crisis as release

kind=sys.argv[1];folder=release.CANDIDATES[kind]/sys.argv[2]
execution=json.loads((folder/'dispatch.json').read_bytes())['execution']
if not execution.startswith(('ibkrv2stg-onboard-render-','ibkrv2stg-month-end-factsheet-')):raise ValueError('Exact scoped execution required')
query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"' | project TimeGenerated, Log_s | order by TimeGenerated desc | take 50"
result=build.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
build.save(folder,'console-'+datetime.now(timezone.utc).strftime('%H%M%S%f'),{'rows':result})
print(json.dumps(result))
