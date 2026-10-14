"""Read an exact-date validated T cache; never silently use stale risk values."""
from .risk_snapshots import RISK_ANALYTICS_VERSION, decoded_payload, snapshot_read_status
from .util import canonical_json


def t_agent_payload(connection, *, tenant_id, as_of, source_snapshot, core_metrics, crisis):
    if not tenant_id:
        raise ValueError('Verified tenant is required')
    row = connection.execute('''SELECT payload_json, payload_sha256, quality_status,
          comparison_version FROM risk_analytics_snapshots
          WHERE tenant_id = %s AND subject_id = %s AND as_of_date = %s
            AND analytics_version = %s''',
          (tenant_id, 'alpha-world-t', as_of, RISK_ANALYTICS_VERSION)).fetchone()
    if snapshot_read_status(row, subject_id='alpha-world-t', as_of=str(as_of)) != 'current':
        raise ValueError('Exact-date validated T risk cache is unavailable')
    payload = decoded_payload(row['payload_json'])
    if (canonical_json(payload.get('source_snapshot')) != canonical_json(source_snapshot)
            or any(payload['metrics'].get(k) != v for k, v in core_metrics.items())
            or canonical_json(payload.get('crisis_replay')) != canonical_json(crisis)):
        raise ValueError('T risk cache differs from current qualified inputs')
    return payload


def ms_agent_payload(connection, *, tenant_id, as_of, source_snapshot, core_metrics, crisis):
    if not tenant_id:raise ValueError('Verified tenant is required')
    row=connection.execute('''SELECT payload_json,payload_sha256,quality_status,comparison_version
        FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s
        AND analytics_version=%s''',(tenant_id,'alpha-world-ms',as_of,RISK_ANALYTICS_VERSION)).fetchone()
    if snapshot_read_status(row,subject_id='alpha-world-ms',as_of=str(as_of))!='current':
        raise ValueError('Exact-date validated MS risk cache is unavailable')
    from .alpha_world_ms_runtime import VERSION
    payload=decoded_payload(row['payload_json'])
    if (payload.get('runtime_version')!=VERSION
        or canonical_json(payload.get('source_snapshot'))!=canonical_json(source_snapshot)
        or any(payload['metrics'].get(k)!=v for k,v in core_metrics.items())
        or canonical_json(payload.get('crisis_replay'))!=canonical_json(crisis)):
        raise ValueError('MS risk cache differs from current qualified inputs')
    return payload
