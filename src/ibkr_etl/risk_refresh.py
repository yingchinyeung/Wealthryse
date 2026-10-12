"""Bounded source-value audits of disposable analytics, never published reports.

Progress lives in cache timing metadata; no new database grants or schema are
needed. Source checks rotate oldest-first across eligible active subjects.
Session locks prevent cooperating risk workers from duplicating expensive work.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math

from .risk_snapshots import (RISK_ANALYTICS_VERSION, RiskInputsNotReady,
    build_risk_snapshot, cache_reuse_status, load_risk_inputs,
    risk_input_fingerprint, validate_risk_input_readiness)
from .util import canonical_json, utc_now_iso


@contextmanager
def risk_date_lock(connection, *, tenant_id, subject_id, as_of):
    key=int.from_bytes(hashlib.sha256(canonical_json(["risk-refresh-v1",tenant_id,subject_id,as_of]).encode()).digest()[:8],
                       byteorder="big",signed=True)
    acquired=connection.execute("SELECT pg_try_advisory_lock(?) AS acquired",(key,)).fetchone()["acquired"]
    connection.commit()
    try:
        yield acquired
    finally:
        connection.rollback()
        if acquired:
            connection.execute("SELECT pg_advisory_unlock(?)",(key,))
            connection.commit()


def mark_cache_check(connection, *, tenant_id, subject_id, as_of, status, reason=None,
                     invalidate=False, expected_source_sha256=None):
    metadata={"source_checked_at":utc_now_iso(),"source_check_status":status}
    if reason:
        metadata["source_check_reason"]=reason
    sql="""UPDATE risk_analytics_snapshots
        SET timings_json=(timings_json - 'source_check_reason') || ?::jsonb,
            quality_status=CASE WHEN ? THEN 'fail' ELSE quality_status END
        WHERE tenant_id=? AND subject_id=? AND as_of_date=? AND analytics_version=?"""
    args=[canonical_json(metadata),invalidate,tenant_id,subject_id,as_of,RISK_ANALYTICS_VERSION]
    if expected_source_sha256 is not None:
        sql+=" AND source_data_sha256=?"
        args.append(expected_source_sha256)
    changed=connection.execute(sql,tuple(args)).rowcount
    connection.commit()
    return changed


def inception_without_comparisons(connection, *, tenant_id, subject_id, as_of):
    """Insufficient inception returns are unavailable, not failed work.

    A missing benchmark contract, later missing coverage or invalid values must
    still block. This never makes an unavailable cache readable.
    """
    if subject_id == 'alpha-world-ms':
        from .subject_read_contract import require_ms_hkd_contract
        # The approved MS contract starts on 20 January 2025. Its full risk
        # payload requires at least two returns (three NAV observations).
        # Bound the exception to these exact inception dates; missing later
        # observations, a changed contract or invalid NAV must still block.
        expected = {'2025-01-20': ['2025-01-20'],
                    '2025-01-21': ['2025-01-20', '2025-01-21']}.get(as_of)
        if expected is None:
            return False
        try:
            require_ms_hkd_contract(connection, tenant_id=tenant_id)
            rows=connection.execute("""SELECT snapshot_date,total_text FROM subject_nav_series
                WHERE tenant_id=? AND subject_id=? AND currency='HKD'
                  AND snapshot_date<=? ORDER BY snapshot_date""", (tenant_id,subject_id,as_of)).fetchall()
            return ([str(row['snapshot_date']) for row in rows] == expected
                    and all(not isinstance(row['total_text'],bool)
                            and math.isfinite(float(row['total_text']))
                            and float(row['total_text']) > 0 for row in rows))
        except (ValueError,TypeError,KeyError,OverflowError):
            return False
    try:
        loaded,_=load_risk_inputs(connection,tenant_id=tenant_id,subject_id=subject_id,as_of=as_of)
    except RiskInputsNotReady:
        return False
    if (loaded['subject_id']!=subject_id or loaded['as_of']!=as_of
            or not loaded['benchmark_id'] or loaded['rows']):
        return False
    row=connection.execute("""SELECT MIN(snapshot_date) AS first_date FROM subject_nav_series
        WHERE tenant_id=? AND subject_id=?""",(tenant_id,subject_id)).fetchone()
    return row is not None and str(row['first_date'])==as_of


def audit_risk_history(connection, *, tenant_id, subjects, audit_limit=20, rebuild_limit=2,
                       inspect_only=False):
    if type(audit_limit) is not int or not 1 <= audit_limit <= 100:
        raise ValueError("History audit limit must be between one and one hundred")
    if type(rebuild_limit) is not int or not 0 <= rebuild_limit <= audit_limit:
        raise ValueError("Rebuild limit must fit inside the bounded audit")
    subjects=sorted(set(subjects))
    if not subjects:
        return {"checked":0,"rebuilt":0,"pending":0,"blocked":0,"busy":0,"unavailable":0,"items":[]}
    records=connection.execute("""SELECT subject_id,as_of_date FROM risk_analytics_snapshots
        WHERE tenant_id=? AND subject_id=ANY(?::text[]) AND analytics_version=?
        ORDER BY (quality_status='fail' AND
                  COALESCE(timings_json->>'source_check_status','') NOT IN ('blocked','insufficient_history')) DESC,
                 timings_json->>'source_checked_at' ASC NULLS FIRST, as_of_date DESC, subject_id
        LIMIT ?""",(tenant_id,subjects,RISK_ANALYTICS_VERSION,audit_limit)).fetchall()
    connection.commit()
    summary={"checked":0,"rebuilt":0,"pending":0,"blocked":0,"busy":0,"unavailable":0,"items":[]}
    for record in records:
        subject_id,as_of=str(record["subject_id"]),str(record["as_of_date"])
        scope={"tenant_id":tenant_id,"subject_id":subject_id,"as_of":as_of}
        with risk_date_lock(connection,**scope) as acquired:
            if not acquired:
                summary["busy"]+=1
                continue
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            existing=connection.execute("""SELECT source_data_sha256,payload_sha256,payload_json,
                quality_status,comparison_version FROM risk_analytics_snapshots
                WHERE tenant_id=? AND subject_id=? AND as_of_date=? AND analytics_version=?""",
                (tenant_id,subject_id,as_of,RISK_ANALYTICS_VERSION)).fetchone()
            if existing is None:
                continue
            summary["checked"]+=1
            try:
                loaded=load_risk_inputs(connection,**scope)
                if loaded[0]["subject_id"]!=subject_id or loaded[0]["as_of"]!=as_of:
                    raise RiskInputsNotReady("Risk input scope mismatch")
                fingerprint=risk_input_fingerprint(loaded[0])
                validate_risk_input_readiness(loaded[0])
                status=cache_reuse_status(existing,inputs=loaded[0],source_sha256=fingerprint)
            except ValueError as error:
                status="blocked"
                reason=str(error) if isinstance(error,RiskInputsNotReady) else "Risk input values failed validation"
                if isinstance(error,RiskInputsNotReady) and inception_without_comparisons(connection,**scope):
                    status='insufficient_history'
                    reason='No comparison-return observations exist at portfolio inception'
            else:
                reason=status
            item={"subject_id":subject_id,"as_of":as_of,"cache_status":status}
            summary["items"].append(item)
            if status=="current":
                if not inspect_only:
                    mark_cache_check(connection,**scope,status="current",expected_source_sha256=existing["source_data_sha256"])
                continue
            if status=='insufficient_history':
                summary['unavailable']+=1
            elif status=="blocked":
                summary["blocked"]+=1
            else:
                summary["pending"]+=1
            if inspect_only:
                continue
            # Withhold the known stale result before costly computation begins.
            mark_cache_check(connection,**scope,status=status,reason=reason,invalidate=True,
                             expected_source_sha256=existing["source_data_sha256"])
            if status in ("blocked","insufficient_history") or summary["rebuilt"] >= rebuild_limit:
                continue
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            # Reload after invalidation commit so concurrent source corrections
            # cannot be rebuilt from the pre-commit transaction's old snapshot.
            try:
                result=build_risk_snapshot(connection,**scope,only_if_changed=True)
            except RiskInputsNotReady as error:
                connection.rollback()
                mark_cache_check(connection,**scope,status="blocked",reason=str(error),invalidate=True)
                summary["blocked"]+=1
                summary["pending"]-=1
                item["cache_status"]="blocked"
                continue
            summary["pending"]-=1
            summary["rebuilt"]+=int(result["status"]=="rebuilt")
            item["result"]=result["status"]
            if result.get('quality_status')=='fail':
                summary['blocked']+=1
                mark_cache_check(connection,**scope,status='blocked',reason='Risk output failed deterministic validation',invalidate=True)
            else:
                mark_cache_check(connection,**scope,status="current")
    return summary
