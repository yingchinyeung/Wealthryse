"""Stable identity tie-break for the reviewed legacy classification lookup."""
def stable_legacy_sector_query(query,args):
    if 'FROM subject_position_series p' not in query:return query
    anchor="ORDER BY ABS(COALESCE(NULLIF(p.market_value_text, ''), '0')::numeric) DESC"
    if not query.lstrip().upper().startswith('SELECT ') or query.count(anchor)!=1 or query.strip().split(anchor)[-1].strip():raise ValueError('Unexpected legacy classification ordering')
    if len(args)!=3 or args[1]!='U16088571' or 'p.subject_id = %s AND p.snapshot_date = %s' not in query:raise ValueError('Exact reviewed legacy subject/date lookup required')
    return query.rstrip()+', p.conid ASC'
