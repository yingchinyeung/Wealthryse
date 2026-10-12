"""Read only bounded QA trial receipts; never retrieve general job logs."""
import json
import control_upgrade_corrected_reports_20261011 as c
query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith 'ibkrv2stg-month-end-factsheet-x51o50x-' | where Log_s contains 'UPGRADE_CORRECTED_SUBJECT' | project Log_s"
print(json.dumps(c.b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)))
