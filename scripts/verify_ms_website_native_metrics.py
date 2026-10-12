"""Bind native reader metrics to the separately protected independent oracle."""
import json
from pathlib import Path

import qualify_ms_website_native_runtime as runtime


def main():
    old=runtime.candidate.rehearsal.STAGE
    audit=json.loads((old/'independent-metrics.json').read_bytes())
    original_qualification=json.loads((old/'qualified.json').read_bytes())
    native_qualification=json.loads((runtime.STAGE/'qualified.json').read_bytes())
    for record in (audit,original_qualification,native_qualification):
        runtime.probe.verify_protected(record)
    original=json.loads((old/'proof.json').read_bytes())
    native=json.loads((runtime.STAGE/'proof.json').read_bytes())
    if (runtime.probe.sha(old/'proof.json')!=original_qualification['proof_sha256']
            or runtime.probe.sha(runtime.STAGE/'proof.json')!=native_qualification['proof_sha256']
            or audit['runtime_proof_sha256']!=original_qualification['proof_sha256']
            or audit['status']!='independent_website_metrics_passed'
            or original['source_binding']['inputs_sha256']!=native['source_binding']['inputs_sha256']
            or original['source_binding']['module_sha256']!=native['source_binding']['module_sha256']
            or original['endpoints']!=native['endpoints']):
        raise ValueError('Native readers differ from independently qualified source equations')
    record={'status':'native_reader_independent_metrics_passed','metrics_verified':40,
        'native_qualification_sha256':runtime.probe.sha(runtime.STAGE/'qualified.json'),
        'independent_oracle_receipt_sha256':runtime.probe.sha(old/'independent-metrics.json'),
        'native_proof_sha256':native_qualification['proof_sha256'],
        'verifier_sha256':runtime.probe.sha(Path(__file__)),
        'image':native_qualification['image'],'persistent_database_writes':0,'factsheets_changed':False}
    runtime.probe.release.build.save(runtime.STAGE,'independent-metrics',
        {**record,'protected_receipt':runtime.probe.retain(record,'ms-website-native-independent-metrics-v1')})
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
