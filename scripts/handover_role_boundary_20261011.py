"""Respect separate read-only web and approval identities; grant nothing."""
original_state=state
def state(db):
    value=original_state(db)
    privileges=value['web_privileges']
    if not privileges['select_ok'] or privileges['insert_ok'] or privileges['update_ok']:
        raise ValueError('Website read-only role boundary changed')
    approval=dict(db.execute("SELECT has_table_privilege('ibkr_stg_approval','report_artifact_releases','SELECT') AS select_ok,has_table_privilege('ibkr_stg_approval','report_artifact_releases','UPDATE') AS update_ok,has_table_privilege('ibkr_stg_approval','report_artifact_releases','INSERT') AS insert_ok").fetchone())
    value['web_privileges']={'web_is_read_only':True,**{'approval_'+k:v for k,v in approval.items()}}
    return value

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('HANDOVER_MIGRATION_ERROR '+json.dumps({'type':type(e).__name__,'sqlstate':getattr(e,'sqlstate',None),'message':str(e) if isinstance(e,ValueError) else None}),flush=True)
        raise SystemExit(1)
