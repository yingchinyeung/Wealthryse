"""Fixed-scope, evidence-bound repair of U16088571 / 2025-11-25.

Default is database-enforced read-only preview. Apply preserves the complete
before-image and consumed prices in a tenant audit event in the same transaction
as replay/cache repair. No network prices, reports, NAV or positions are written.
"""
import hashlib
import json
import math
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'src'))
import psycopg
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.crisis_replay import CALCULATION_VERSION, load_crisis_replay
from ibkr_etl import risk_snapshots as risk
from ibkr_etl.util import utc_now_iso
from inspect_crisis_gap import verify_stored_replay

SUBJECT='U16088571'
DAY='2025-11-25'
EVENT='crisis_replay_repair_20261001'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),default=str,allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def validate_repair_inputs(data,check,expected):
    if data.get('subject_id')!=SUBJECT or data.get('as_of')!=DAY:
        raise ValueError('Repair is restricted to the approved account/date')
    if len(expected)!=64 or digest(data)!=expected:
        raise ValueError('Inputs changed since reviewed preview')
    if {r['calculation_version'] for r in data['legacy']}!={'as-of-holdings-crisis-replay-v2'} or len(data['legacy'])!=121:
        raise ValueError('Unexpected historical baseline; refuse overwrite')
    if check.get('independent_decimal_verified') is not True or check.get('exact_holdings_reconciled') is not True:
        raise ValueError('Independent reconciliation is required')
    differences=[check['maximum_path_difference'],check['maximum_drawdown_difference']]
    if any(not math.isfinite(x) or x<0 or x>0.000001 for x in differences):
        raise ValueError('Discrepancy exceeds reviewed repair bound')


def read_inputs(raw):
    def rows(sql,args=()):
        return [dict(row) for row in raw.execute(sql,args).fetchall()]
    scope=(SUBJECT,DAY)
    holdings=rows("""SELECT p.conid,p.currency,SUM(CAST(p.market_value_text AS DOUBLE PRECISION)) AS market_value
      FROM subject_position_series p LEFT JOIN instruments i ON i.conid=p.conid
      WHERE p.tenant_id=app_tenant_id() AND p.subject_id=%s AND p.snapshot_date=%s
        AND UPPER(COALESCE(i.symbol,'')) NOT LIKE '%%.REC'
      GROUP BY p.conid,p.currency ORDER BY p.conid,p.currency""",scope)
    mappings=rows("""SELECT * FROM crisis_replay_components WHERE tenant_id=app_tenant_id()
      AND subject_id=%s AND holdings_as_of=%s ORDER BY conid""",scope)
    nav=rows("""SELECT total_text FROM subject_nav_series WHERE tenant_id=app_tenant_id()
      AND subject_id=%s AND snapshot_date=%s""",scope)
    if len(nav)!=1:
        raise ValueError('Missing or ambiguous NAV')
    symbols=sorted({r['source_symbol'] for r in mappings}|{'SPY'})
    prices=rows("""SELECT * FROM market_data_prices WHERE tenant_id=app_tenant_id()
      AND instrument_id LIKE 'crisis-replay-%%' AND symbol=ANY(%s)
      AND price_date BETWEEN '2008-09-15' AND '2009-03-09'
      ORDER BY symbol,price_date,price_type""",(symbols,))
    legacy=rows("""SELECT * FROM crisis_replay_daily WHERE tenant_id=app_tenant_id()
      AND subject_id=%s AND holdings_as_of=%s ORDER BY observation_date""",scope)
    cache=rows("""SELECT * FROM risk_analytics_snapshots WHERE tenant_id=app_tenant_id()
      AND subject_id=%s AND as_of_date=%s ORDER BY analytics_version""",scope)
    return {'subject_id':SUBJECT,'as_of':DAY,'holdings':holdings,'mappings':mappings,
            'nav':nav[0]['total_text'],'prices':prices,'legacy':legacy,'cache':cache}


def protected_fingerprint(raw):
    # Manifest metadata only: no artifact downloads, mutation or publication.
    items={}
    for table in ('reporting_snapshots','report_generation_runs','report_artifact_releases'):
        # row_to_json captures every column; order by its canonical text avoids
        # assuming artifact table key names across migrations.
        result=raw.execute('SELECT row_to_json(t)::text AS value FROM '+table+
                           ' t WHERE tenant_id=app_tenant_id() ORDER BY row_to_json(t)::text').fetchall()
        items[table]=[row['value'] for row in result]
    return digest(items)


def main():
    mode=globals().get('REPAIR_MODE','preview')
    expected=globals().get('EXPECTED_INPUT_SHA256','')
    if mode not in ('preview','apply','verify'):
        raise ValueError('Unknown repair mode')
    tenant=os.environ['PORTFOLIO_TENANT_ID']
    readonly=mode!='apply'
    options='-c statement_timeout=120000 -c lock_timeout=5000'+(' -c default_transaction_read_only=on' if readonly else '')
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=20,options=options,
                         row_factory=_hybrid_row_factory) as raw:
        connection=PostgresConnection(raw)
        raw.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'+(' READ ONLY' if readonly else ''))
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        if mode=='apply':
            raw.execute("SELECT pg_advisory_xact_lock(20261001,20251125)")
        data=read_inputs(raw)
        source_hash=digest(data)
        check=verify_stored_replay(data['holdings'],data['mappings'],data['nav'],data['prices'],data['legacy'],include_replay=True)
        replay=check.pop('replay')
        summary={'mode':mode,'subject_id':SUBJECT,'as_of':DAY,'input_sha256':source_hash,**check}
        if mode=='preview':
            print('CRISIS_REPAIR '+canonical(summary),flush=True)
            return
        if mode=='apply':
            # Exact observed case: differences smaller than 0.0001 percentage
            # points. Larger or unexplained changes require a fresh review.
            validate_repair_inputs(data,check,expected)
            protected=protected_fingerprint(raw)
            now=utc_now_iso()
            before_sha=digest(data['legacy'])
            audit={'authorization':'Owner requested repair of sampled discrepancy',
                   'subject_id':SUBJECT,'as_of':DAY,'input_sha256':source_hash,
                   'before_image':data,'recomputed_daily':replay['daily'],
                   'validation':check,'protected_manifests_sha256':protected}
            audit_id=raw.execute("""INSERT INTO audit_events(occurred_at,actor_type,actor_id,event_type,
                 subject_id,outcome,details_json) VALUES (%s,'service','scoped-crisis-repair',%s,%s,
                 'repaired',%s) RETURNING audit_event_id""",(now,EVENT,SUBJECT,canonical(audit))).fetchone()['audit_event_id']
            for row in replay['daily']:
                cursor=raw.execute("""UPDATE crisis_replay_daily SET normalized_value=%s,drawdown=%s,
                    calculation_version=%s,quality_status='warn',generated_at=%s
                  WHERE tenant_id=app_tenant_id() AND subject_id=%s AND holdings_as_of=%s
                    AND observation_date=%s AND calculation_version='as-of-holdings-crisis-replay-v2'""",
                    (row['normalized_value'],row['drawdown'],CALCULATION_VERSION,now,SUBJECT,DAY,row['observation_date']))
                if cursor.rowcount!=1:
                    raise ValueError('Historical row count changed')
            payload,timings,risk_hash=risk.compute_risk_snapshot(connection,tenant_id=tenant,subject_id=SUBJECT,as_of=DAY)
            if payload['quality_status']=='fail' or not math.isclose(
                    payload['metrics'].get('crisis_maximum_drawdown',float('nan')),replay['maximum_drawdown'],abs_tol=1e-12):
                raise ValueError('Rebuilt risk package did not include verified crisis metric')
            risk.store_risk_snapshot(connection,tenant_id=tenant,subject_id=SUBJECT,as_of=DAY,
                                     payload=payload,timings=timings,source_data_sha256=risk_hash)
            if protected_fingerprint(raw)!=protected:
                raise ValueError('Protected reporting manifests changed')
            summary.update(database_changed=True,audit_event_id=audit_id,before_daily_sha256=before_sha,
                           report_manifests_unchanged=True)
        current=load_crisis_replay(connection,account_id=SUBJECT,as_of=DAY)
        inputs,_=risk.load_risk_inputs(connection,tenant_id=tenant,subject_id=SUBJECT,as_of=DAY)
        cached=raw.execute("""SELECT * FROM risk_analytics_snapshots WHERE tenant_id=app_tenant_id()
            AND subject_id=%s AND as_of_date=%s AND analytics_version=%s""",(SUBJECT,DAY,risk.RISK_ANALYTICS_VERSION)).fetchone()
        status=risk.cache_reuse_status(cached,inputs=inputs,source_sha256=risk.risk_input_fingerprint(inputs))
        value=risk.decoded_payload(cached['payload_json'])['metrics'].get('crisis_maximum_drawdown')
        if current is None or status!='current' or value is None or not math.isclose(value,replay['maximum_drawdown'],abs_tol=1e-12):
            raise ValueError('Post-repair replay/cache verification failed')
        summary.update(cache_status=status,crisis_metric_available=True,target_version=current['calculation_version'])
        raw.commit()
        print('CRISIS_REPAIR '+canonical(summary),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('CRISIS_REPAIR '+canonical({'status':'error','error_type':type(error).__name__,
              'reason':str(error) if isinstance(error,ValueError) else 'database or runtime error; transaction rolled back'}),flush=True)
        raise SystemExit(1)
