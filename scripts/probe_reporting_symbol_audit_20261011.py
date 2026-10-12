"""Read-only identity provenance audit, not release qualification."""
import hashlib,json,os,socket
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q

def main():
    if os.getuid()!=10001 or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}: raise ValueError('Private QA boundary required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest: raise ValueError('Installed source drift')
    with q.connect(q.TEST_DB) as db:
        if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on': raise ValueError('Read-only required')
        def state():
            return hashlib.sha256('\n'.join(r['h'] for r in db.execute('SELECT md5(row_to_json(t)::text) AS h FROM reporting_snapshots t ORDER BY 1').fetchall()).encode()).hexdigest()
        if state()!=EXPECTED: raise ValueError('Private snapshot drift')
        instrument=db.execute("SELECT conid,symbol,description,asset_category,currency,isin,first_seen_at,last_seen_at,source_json FROM instruments WHERE conid='804144296'").fetchone()
        raw=instrument.pop('source_json'); attrs=json.loads(raw)
        instrument['source_sha256']=hashlib.sha256(raw.encode()).hexdigest()
        instrument['source_identity']={k:attrs[k] for k in ('conid','symbol','description','isin','securityID','securityIDType','reportDate','date','listingExchange','assetCategory','currency') if k in attrs}
        flex=db.execute("""SELECT data_json::jsonb->>'symbol' AS symbol,
            data_json::jsonb->>'isin' AS isin,data_json::jsonb->>'securityID' AS security_id,
            section,record_type,min(report_date) AS first_date,max(report_date) AS last_date,count(*) AS rows
            FROM flex_rows WHERE conid='804144296' GROUP BY 1,2,3,4,5 ORDER BY 1,4,5""").fetchall()
        if len(flex)>35: raise ValueError('Unexpected identity provenance volume')
        samples=db.execute("""SELECT report_date,section,record_type,row_sha256,
            data_json::jsonb->>'symbol' AS symbol,data_json::jsonb->>'description' AS description,
            data_json::jsonb->>'isin' AS isin,data_json::jsonb->>'securityID' AS security_id
            FROM flex_rows WHERE conid='804144296' AND report_date IS NOT NULL
            ORDER BY report_date DESC,section,record_type LIMIT 4""").fetchall()
        summaries=[]
        for row in db.execute("SELECT subject_id,snapshot_payload_json FROM reporting_snapshots WHERE period_end='2026-09-30' AND status IN ('frozen','approved') ORDER BY subject_id").fetchall():
            p=json.loads(row['snapshot_payload_json'])
            labels={name:[{'conid':r['conid'],'symbol':r.get('symbol'),'description':r.get('description')} for r in entries if str(r.get('conid'))=='804144296'] for name,entries in (
                ('holdings',p.get('holdings',{}).get('holdings',[])),('classifications',p.get('sector_allocation',{}).get('classifications',[])))}
            summaries.append({'subject':row['subject_id'],'labels':labels})
        if len(summaries)!=5 or state()!=EXPECTED: raise ValueError('QA state differs')
    print('REPORTING_SYMBOL_AUDIT_FINAL '+json.dumps({'passed':True,'scope':'Identity provenance audit only; not runtime acceptance','instrument':instrument,'flex_groups':flex,'latest_flex_samples':samples,'subjects':summaries,'source_hashes':SOURCE_HASHES,'image':IMAGE,'database_writes':0,'production_connection_used':False},default=str),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_SYMBOL_AUDIT_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:300]}),flush=True)
        raise SystemExit(1)
