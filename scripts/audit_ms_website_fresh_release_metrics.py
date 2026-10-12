"""Apply the unchanged independent equations to the fresh exact-image proof."""
from pathlib import Path

import audit_ms_website_benchmark_metrics as audit
import qualify_ms_website_fresh_release_runtime as fresh


if __name__=='__main__':
    audit.rehearsal.STAGE=fresh.STAGE
    audit.rehearsal.probe.FOLDER=fresh.FOLDER/'fresh-inputs-v1'
    audit.main()
    record={'status':'fresh_native_independent_metrics_bound',
        'wrapper_sha256':fresh.p.sha(Path(__file__)),
        'fresh_runtime_controller_sha256':fresh.p.sha(Path(fresh.__file__)),
        'independent_metrics_sha256':fresh.p.sha(fresh.STAGE/'independent-metrics.json'),
        'persistent_database_writes':0,'factsheets_changed':False}
    fresh.p.release.build.save(fresh.STAGE,'independent-metrics-binding',
        {**record,'protected_receipt':fresh.p.retain(record,'ms-website-fresh-native-metrics-binding-v1')})
