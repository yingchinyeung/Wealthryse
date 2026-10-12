"""Scoped, evidence-gated Alpha World MS reporting deployment controller.

Uses the existing ACR build/scanner and protected evidence mechanisms. Updates
only two reporting job image digests, preserving identities, schedules and all
other settings. Never activates onboarding or changes source data.
"""
from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / ".work/alpha-ms-azure-v14r7-20261007"
for path in (ROOT / "src", ROOT / "scripts", ROOT / ".work/system-improvements-20261006",
             ROOT / ".work/alpha-ms-azure-controller-runtime"):
    sys.path.insert(0, str(path))
import build_report_runtime_candidate as build
from deploy_metadata_monitor import request, BASE
from run_specialist_gateway_read_verification import retain

TAG = "ibkr-platform:alpha-ms-layout-v14r7-20261007"
build.FOLDER = FOLDER
build.TAG = TAG
JOBS = ("ibkrv2stg-onboard-render", "ibkrv2stg-month-end-factsheet")
LAYOUT = "subject-completed-factsheet-model-v14r7-alpha-world-ms-validated-charts"
CONTAINER = "internal-reports"
PREFIX = "review/alpha-world-ms/azure-v14r7-20261007/"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(name):
    return json.loads((FOLDER / (name + ".json")).read_text(encoding="utf-8"))["record"]


def job_path(name):
    if name not in JOBS:
        raise ValueError("Reporting target outside scope")
    return BASE + "Microsoft.App/jobs/" + name


def get_job(name):
    return request("GET", job_path(name) + "?api-version=2024-03-01")


def invariant(job):
    template = copy.deepcopy(job["properties"]["template"])
    for c in template["containers"]:
        c.pop("image", None)
    return sha(json.dumps({"identity": job.get("identity"), "template": template,
        "configuration": job["properties"]["configuration"],
        "environmentId": job["properties"]["environmentId"]}, sort_keys=True).encode())


def image():
    native = read("build-completed")
    if native["native_status"] != "Succeeded":
        raise ValueError("Successful exact native build required")
    output = native["output_images"][0]
    return output["registry"] + "/" + output["repository"] + "@" + output["digest"]


def prepare():
    if (FOLDER / "source-intent.json").exists():
        raise ValueError("Frozen intent exists; do not replace")
    context = FOLDER / "context"
    context.mkdir()
    names = ["src/ibkr_etl/monthly_artifacts.py", "src/ibkr_etl/alpha_world_ms_analytics.py",
             "src/ibkr_etl/alpha_world_ms_pdf_validation.py", "src/ibkr_etl/market_calendar.py",
             "scripts/generate_month_end_package.py",
             "scripts/render_alpha_world_ms_azure_batch.py", "tests/test_monthly_artifacts.py",
             "tests/test_alpha_world_ms_chart_style.py", "tests/test_alpha_world_ms_analytics.py",
             "tests/test_alpha_world_ms_pdf_accuracy.py",
             "tests/test_month_end_package_readiness.py"]
    installed = {}
    for name in names:
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())  # exact mechanical freeze
        if not name.startswith("tests/"):
            installed[name] = sha(target.read_bytes())
    release = {"layout_version": LAYOUT, "installed_files_sha256": installed,
        "capture_sha256": sha((ROOT / ".work/alpha-ms-factsheets-v13-20261007/capture.json").read_bytes()),
        "corrected_manifest_sha256": sha((ROOT / "output/pdf/AlphaWorld_MS_Recovered_20261007/manifest.json").read_bytes()),
        "base_image": "ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:5ce97d985e2b0b4e0badbb90dcf716f749df73b36644312c184385e3dfe57f95",
        "owner_approved_factsheet_benchmark_label": "HIBOR + Spread",
        "owner_approved_spy_allocation_caption": "S&P500",
        "owner_confirmed_interest_basis": "Interest already reflected in Alpha World MS NAV; no additional projected financing charge",
        "distribution_title": "MC Annualized Return Distribution - After Interest",
        "separate_projected_financing_model_present": False,
        "comparison_benchmark_and_financial_methodology_changed": False,
        "source_data_changed": False, "report_release_authorized": False}
    (context / "ALPHA_MS_RENDER_RELEASE.json").write_text(json.dumps(release, indent=2), encoding="utf-8")
    (context / "Dockerfile").write_bytes((FOLDER / "recipe.Dockerfile").read_bytes())
    before = {name: get_job(name) for name in JOBS}
    for name, job in before.items():
        if len(job["properties"]["template"]["containers"]) != 1 or job["properties"]["configuration"]["replicaRetryLimit"] != 0:
            raise ValueError("Unexpected reporting worker settings")
    build.save("source-intent", {"prepared_at": datetime.now(timezone.utc).isoformat(),
        "tag": TAG, "layout_version": LAYOUT,
        "files_sha256": {p.relative_to(context).as_posix(): sha(p.read_bytes()) for p in context.rglob("*") if p.is_file()},
        "before_jobs": before, "before_invariants": {n: invariant(j) for n, j in before.items()},
        "source_data_changed": False, "target_jobs": JOBS, "scope": "reporting_only",
        "production_promotion": False})
    print(json.dumps({"prepared": True, "files": len(names) + 2, "targets": JOBS}))


def qualify_log():
    build.verify_context()
    ack = read("build-ack")
    native = build.az("acr", "task", "show-run", "-r", build.REGISTRY, "--run-id", ack["run"])
    if native["status"] != "Succeeded":
        raise ValueError("Native build not successful")
    result = subprocess.run(["az.cmd", "acr", "task", "logs", "-r", build.REGISTRY,
        "--run-id", ack["run"]], capture_output=True, encoding="utf-8", timeout=120,
        env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1"))
    if result.returncode:
        raise ValueError("Build log retrieval failed")
    content = result.stdout + result.stderr
    tests = re.findall(r"Ran (\d+) tests? in [^\n]+\n\s*OK(?:\s|$)", content)
    qualified = sum(map(int, tests)) >= 40 and all(marker in content for marker in (
        "PASS: report templates, PowerPoint runtime and native chart canvas",
        "PASS: Azure historical and future MS reporting entrypoints, layout v14r7"))
    (FOLDER / "native-build.log").open("x", encoding="utf-8").write(content)
    build.save("native-build-qualified" if qualified else "native-build-rejected",
        {"run": ack["run"], "qualified": qualified, "linux_tests": sum(map(int, tests)),
         "log_sha256": sha(content.encode()), "image": image()})
    print(json.dumps({"qualified": qualified, "linux_tests": sum(map(int, tests))}))


def reconcile_build():
    if (FOLDER / "build-ack.json").exists() or not (FOLDER / "build-intent.json").exists():
        raise ValueError("Only an unacknowledged original dispatch can be reconciled")
    build.verify_context()
    after = datetime.fromisoformat(read("build-intent")["checked_at"])
    runs = build.az("acr", "task", "list-runs", "-r", build.REGISTRY, "--top", "5")
    matches = [r for r in runs if datetime.fromisoformat(r["createTime"].replace("Z", "+00:00")) >= after]
    if len(matches) != 1:
        raise ValueError("Unique original native build required; do not redispatch")
    build.save("build-ack", {"run": matches[0]["runId"], "redispatched": False,
        "reconciled_from_native_metadata": True, "exact_output_identity_pending": True})
    print(json.dumps({"reconciled": matches[0]["runId"], "status": matches[0]["status"]}))


def require_candidate():
    build.verify_context()
    if not read("native-build-qualified")["qualified"]:
        raise ValueError("Linux runtime qualification required")
    scan = read("scan-completed")
    if scan["image"] != image() or scan["assessment"]["status"] != "pass":
        raise ValueError("Exact clean candidate scan required")


def dispatch():
    require_candidate()
    if (FOLDER / "azure-dispatch-intent.json").exists():
        raise ValueError("Dispatch intent exists; reconcile instead of repeating")
    job = get_job(JOBS[0])
    if invariant(job) != read("source-intent")["before_invariants"][JOBS[0]]:
        raise ValueError("Reporting worker configuration drift")
    runs = request("GET", job_path(JOBS[0]) + "/executions?api-version=2024-03-01").get("value", [])
    if any(r["properties"]["status"] in ("Running", "Processing") for r in runs):
        raise ValueError("Onboarding worker busy")
    template = copy.deepcopy(job["properties"]["template"])
    template = {k: v for k, v in template.items() if v is not None}
    c = template["containers"][0]
    c["image"] = image()
    c["command"] = ["python"]
    c["args"] = ["scripts/render_alpha_world_ms_azure_batch.py"]
    c["env"].append({"name": "ALPHA_MS_IMAGE_DIGEST", "value": image().split("@")[1]})
    # Existing private-network identity and credentials, no additional grants.
    build.save("azure-dispatch-intent", {"template": template, "image": image(),
        "job": JOBS[0], "persistent_configuration_changed": False,
        "source_data_changed": False, "reports_released": False})
    result = request("POST", job_path(JOBS[0]) + "/start?api-version=2024-03-01", data=template)
    build.save("azure-dispatch", {"execution": result["name"], "image": image()})
    print(json.dumps({"execution": result["name"], "image": image()}))


def inspect_execution():
    ack = read("azure-dispatch")
    run = request("GET", job_path(JOBS[0]) + "/executions/" + ack["execution"] + "?api-version=2024-03-01")
    print(json.dumps({"execution": ack["execution"], "status": run["properties"]["status"],
                      "start": run["properties"].get("startTime"), "end": run["properties"].get("endTime")}))


def storage():
    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient
    return BlobServiceClient("https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net", credential=AzureCliCredential())


def download():
    ack = read("azure-dispatch")
    run = request("GET", job_path(JOBS[0]) + "/executions/" + ack["execution"] + "?api-version=2024-03-01")
    if run["properties"]["status"] != "Succeeded":
        raise ValueError("Azure batch not successful")
    wanted = read("azure-dispatch-intent")["template"]["containers"][0]
    actual = run["properties"]["template"]["containers"][0]
    if any(actual[k] != wanted[k] for k in ("image", "command", "args")):
        raise ValueError("Executed Azure container differs")
    service = storage()
    raw = service.get_blob_client(CONTAINER, PREFIX + "manifest.json").download_blob().readall()
    manifest = json.loads(raw)
    if manifest["count"] != 21 or manifest["image_digest"] != image().split("@")[1] or manifest["layout_version"] != LAYOUT:
        raise ValueError("Cloud-render manifest mismatch")
    folder = ROOT / "output/pdf/AlphaWorld_MS_Azure_Factsheets_v14r7"
    folder.mkdir(exist_ok=True)
    for entry in manifest["artifacts"]:
        content = service.get_blob_client(CONTAINER, entry["blob_name"]).download_blob().readall()
        if sha(content) != entry["sha256"]:
            raise ValueError("Cloud PDF download hash mismatch")
        target = folder / Path(entry["blob_name"]).name
        if target.exists() and target.read_bytes() != content:
            raise ValueError("Existing downloaded output differs")
        target.write_bytes(content)
    (folder / "manifest.json").write_bytes(raw)
    build.save("azure-render-qualified", {"execution": ack["execution"], "image": image(),
        "manifest": manifest, "manifest_sha256": sha(raw), "downloaded_pdfs": 21,
        "native_execution": run, "source_data_changed": False})
    print(json.dumps({"azure_rendered": 21, "execution": ack["execution"], "folder": str(folder)}))


def promote():
    require_candidate()
    rendered = read("azure-render-qualified")
    visual = read("pdf-verification")
    if rendered["image"] != image() or visual["status"] != "pass" or visual["count"] != 21:
        raise ValueError("Exact live Azure and PDF acceptance required")
    intent = read("source-intent")
    before = {name: get_job(name) for name in JOBS}
    for name, job in before.items():
        if invariant(job) != intent["before_invariants"][name] or job["properties"]["template"]["containers"][0]["image"] != intent["before_jobs"][name]["properties"]["template"]["containers"][0]["image"]:
            raise ValueError("Target drift; do not overwrite another release")
    build.save("promotion-intent", {"image": image(), "before_jobs": before,
        "rollback_images": {n: j["properties"]["template"]["containers"][0]["image"] for n, j in before.items()},
        "approval": "Owner requested Azure historical and future MS factsheet rendering",
        "source_data_changed": False})
    for name in JOBS:
        build.az("containerapp", "job", "update", "-g", "rg-ibkr-database-v11", "-n", name, "--image", image())
        after = get_job(name)
        if invariant(after) != intent["before_invariants"][name] or after["properties"]["template"]["containers"][0]["image"] != image():
            raise ValueError("Post-release settings mismatch; stop and reconcile")
        build.save("promoted-" + name, {"image": image(), "invariant_unchanged": True,
            "job": name, "after": after, "rollback_image": before[name]["properties"]["template"]["containers"][0]["image"]})
    build.save("promotion-completed", {"image": image(), "jobs": JOBS, "invariants_unchanged": True,
        "azure_rendered_factsheets": 21, "layout": LAYOUT, "source_data_changed": False,
        "onboarding_activated": False, "reports_released": False})
    print(json.dumps(read("promotion-completed")))


def monthend_probe():
    if read("promotion-completed")["image"] != image():
        raise ValueError("Installed exact renderer required")
    job = get_job(JOBS[1])
    if job["properties"]["template"]["containers"][0]["image"] != image():
        raise ValueError("Scheduled worker image differs")
    if (FOLDER / "monthend-probe-intent.json").exists():
        raise ValueError("Original qualification already dispatched; inspect only")
    source = (ROOT / "scripts/verify_alpha_world_ms_monthend_runtime.py").read_bytes()
    template = copy.deepcopy(job["properties"]["template"])
    template = {k: v for k, v in template.items() if v is not None}
    container = template["containers"][0]
    container["command"] = ["python"]
    container["args"] = ["-c", "import base64;exec(compile(base64.b64decode('" + base64.b64encode(source).decode()
        + "'),'installed-ms-monthend-qualification','exec'))"]
    container["env"].append({"name": "ALPHA_MS_IMAGE_DIGEST", "value": image().split("@")[1]})
    build.save("monthend-probe-intent", {"source_sha256": sha(source), "template": template,
        "persistent_job_invariant": invariant(job), "imports_installed_renderer_only": True})
    run = request("POST", job_path(JOBS[1]) + "/start?api-version=2024-03-01", data=template)
    build.save("monthend-probe-dispatch", {"execution": run["name"], "image": image()})
    print(json.dumps({"execution": run["name"], "image": image()}))


def verify_monthend_probe():
    ack = read("monthend-probe-dispatch")
    native = request("GET", job_path(JOBS[1]) + "/executions/" + ack["execution"] + "?api-version=2024-03-01")
    if native["properties"]["status"] != "Succeeded":
        print(json.dumps({"execution": ack["execution"], "status": native["properties"]["status"]}))
        return
    actual = native["properties"]["template"]["containers"][0]
    expected = read("monthend-probe-intent")["template"]["containers"][0]
    if any(actual[k] != expected[k] for k in ("image", "command", "args")):
        raise ValueError("Executed installed-runtime probe differs")
    if invariant(get_job(JOBS[1])) != read("monthend-probe-intent")["persistent_job_invariant"]:
        raise ValueError("Persistent scheduled worker changed during qualification")
    prefix = "qualification/alpha-world-ms/monthend-v14r7-20261007/"
    service = storage()
    proof = json.loads(service.get_blob_client(CONTAINER, prefix + "verification.json").download_blob().readall())
    if (proof["status"] != "passed" or proof["image_digest"] != image().split("@")[1]
            or proof["layout_version"] != LAYOUT or proof["database_writes"] != 0):
        raise ValueError("Live scheduled worker proof mismatch")
    content = service.get_blob_client(CONTAINER, proof["private_pdf_blob"]).download_blob().readall()
    if sha(content) != proof["pdf_sha256"]:
        raise ValueError("Scheduled worker artifact integrity failure")
    build.save("monthend-probe-qualified", {"execution": ack["execution"], "proof": proof,
        "native_execution": native, "persistent_settings_unchanged": True})
    print(json.dumps({"execution": ack["execution"], "status": "passed", "layout": LAYOUT}))


def finish():
    require_candidate()
    if read("monthend-probe-qualified")["proof"]["status"] != "passed":
        raise ValueError("Scheduled identity live qualification required")
    jobs = {name: get_job(name) for name in JOBS}
    intent = read("source-intent")
    for name, job in jobs.items():
        if (job["properties"]["template"]["containers"][0]["image"] != image()
                or invariant(job) != intent["before_invariants"][name]):
            raise ValueError("Final installed reporting job verification failed")
    build.save("completion", {"status": "azure_renderer_deployed_and_qualified",
        "image": image(), "layout_version": LAYOUT, "resource_group": "rg-ibkr-database-v11",
        "jobs": JOBS, "cloud_batch_execution": read("azure-dispatch")["execution"],
        "scheduled_identity_qualification_execution": read("monthend-probe-dispatch")["execution"],
        "azure_rendered_monthly_factsheets": 21, "first_month": "2025-01-31", "last_month": "2026-09-30",
        "pdf_verification": {k: v for k, v in read("pdf-verification").items() if k != "checks"},
        "scan_counts": read("scan-completed")["assessment"]["counts"],
        "linux_regression_tests": read("native-build-qualified")["linux_tests"],
        "scheduled_job_cron_utc": jobs[JOBS[1]]["properties"]["configuration"]["scheduleTriggerConfig"]["cronExpression"],
        "identities_schedules_credentials_and_retry_limits_unchanged": True,
        "ibkr_source_data_changed": False, "database_records_changed": False,
        "reports_released": False, "website_downloads_replaced": False, "onboarding_activated": False,
        "owner_requested_note_blocks_removed": ["NAV and daily-risk basis", "bootstrap methodology", "fixed-unit allocation structure"],
        "financial_calculation_code_unchanged": True,
        "factsheet_benchmark_label": "HIBOR + Spread",
        "spy_allocation_caption": "S&P500",
        "owner_confirmed_interest_basis": "Interest already reflected in Alpha World MS NAV; no additional projected financing charge",
        "distribution_title": "MC Annualized Return Distribution - After Interest",
        "distribution_title_changed": True,
        "comparison_benchmark_and_financial_methodology_changed": False,
        "historical_inputs": "19 unchanged frozen inputs plus the two already reconciled August/September private corrections",
        "future_months": "Installed month-end path uses v14r7 with the S&P500 allocation caption, unchanged SPY price proxy, HIBOR + Spread heading and After Interest distribution title on the owner-confirmed NAV-interest basis; actual future rendering still requires active onboarding, month-end registration and validated data dependencies",
        "private_azure_output_prefix": CONTAINER + "/" + PREFIX})
    print(json.dumps({"status": "complete", "azure_rendered": 21, "workers_verified": 2,
        "activation_and_release_separate": True}))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) == 2 else ""
    handlers = {"prepare": prepare, "log": qualify_log, "dispatch": dispatch,
                "execution": inspect_execution, "download": download, "promote": promote,
                "reconcile": reconcile_build, "monthend-probe": monthend_probe,
                "verify-monthend-probe": verify_monthend_probe, "finish": finish}
    if stage in handlers:
        handlers[stage]()
    elif stage == "scan":
        import scan_report_runtime_candidate
        scan_report_runtime_candidate.main()
    elif stage in ("build", "inspect"):
        build.main()
    else:
        raise ValueError("Unknown stage")
