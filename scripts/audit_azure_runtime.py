"""Read-only, secret-free operational inventory; writes only a local audit file."""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

AZ = r"C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe"
RG = "rg-ibkr-database-v11"


def az(*args: str):
    result = subprocess.run([AZ, "-m", "azure.cli", *args, "-o", "json", "--only-show-errors"],
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"Azure read failed: {' '.join(args[:3])} ({result.returncode})")
    return json.loads(result.stdout or "null")


def main():
    jobs = az("containerapp", "job", "list", "-g", RG, "--query",
              "[].{name:name,trigger:properties.configuration.triggerType,"
              "cron:properties.configuration.scheduleTriggerConfig.cronExpression,"
              "retry:properties.configuration.replicaRetryLimit,"
              "timeout:properties.configuration.replicaTimeout,"
              "containers:properties.template.containers[].{name:name,image:image,resources:resources}}")
    def recent(job):
        job["recent_executions"] = az("containerapp", "job", "execution", "list",
            "-g", RG, "-n", job["name"], "--query",
            "[:3].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}")
        return job
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(recent, jobs))
    result = {"captured_at": datetime.now(timezone.utc).isoformat(), "resource_group": RG, "jobs": jobs}
    path = Path(__file__).resolve().parents[1] / ".work" / "system-audit-20261001" / "jobs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    for job in jobs:
        latest = job["recent_executions"][:1]
        print(job["name"], job["cron"], json.dumps(latest))
    print("Saved", path)


if __name__ == "__main__":
    main()
