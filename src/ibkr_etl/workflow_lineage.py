"""Read-only bridge from approved contracts and real producer data to lineage.

No dispatch or stage approval. Registry agreement and value fingerprints are
necessary, not proof of complete ingestion or correct derived methodology.
"""
from datetime import date
from decimal import Decimal, DecimalException
from uuid import UUID
import json
from .subject_onboarding import validate_typed_runtime_contract, dependencies_for_definition
from .workflow_ledger import digest
from .market_calendar import benchmark_session_on_or_before, CALENDAR_VERSION

VERSION='workflow-producer-lineage-v1'


def _day(value):
    return value if type(value) is date else date.fromisoformat(str(value))


def _effective(row, cutoff):
    return _day(row['effective_from']) <= cutoff and (
        row.get('effective_to') is None or _day(row['effective_to']) >= cutoff)


def _json(value):
    return json.loads(value) if isinstance(value,str) else value


def _number(value):
    try:return Decimal(str(value))
    except DecimalException as error:raise ValueError('Invalid producer numerical value') from error


def graph(*, tenant_id, as_of, subjects, definitions, dependencies, roots):
    """Exact effective registry agreement, typed endpoints and bounded cycle checks."""
    tenant_id=str(UUID(str(tenant_id)));cutoff=_day(as_of)
    records={}
    for row in subjects:
        if str(row['tenant_id']) != tenant_id or row['subject_id'] in records:
            raise ValueError('Ambiguous or cross-tenant subject registry')
        records[row['subject_id']]=dict(row)
    if not roots or len(set(roots))!=len(roots) or len(records)>1000:
        raise ValueError('Explicit bounded unique roots required')
    for row in (*definitions,*dependencies):
        if str(row['tenant_id']) != tenant_id:
            raise ValueError('Cross-tenant lineage record')
    ordered=[];nodes={};edges=[];visiting=set()
    def visit(subject):
        if subject in visiting:raise ValueError('Cross-subject dependency cycle')
        if subject in nodes:return
        if subject not in records:raise ValueError('Unregistered dependency endpoint')
        if len(visiting)>=64:raise ValueError('Dependency depth limit exceeded')
        row=records[subject];kind=row['subject_type']
        if row['lifecycle_status']!='active':raise ValueError('Dependency is not active')
        visiting.add(subject)
        configured=[r for r in dependencies if r['subject_id']==subject and _effective(r,cutoff)]
        current=[r for r in definitions if r['subject_id']==subject and _effective(r,cutoff)
                 and r['approval_status']=='approved']
        definition=None;identity=None
        if kind in ('filtered_portfolio','composite_portfolio'):
            if len(current)!=1:raise ValueError('Missing or ambiguous approved calculation')
            d=current[0]
            definition=validate_typed_runtime_contract(subject,kind,d['configuration_json'],d['configuration_sha256'])
            if (d['subject_type']!=kind or d['calculation_kind']!=definition['calculation_kind']
                    or row['base_currency']!=definition['base_currency']):
                raise ValueError('Calculation and subject registry disagree')
            identity={'definition_id':str(UUID(str(d['definition_id']))),
                      'definition_version':d['definition_version'],'configuration_sha256':d['configuration_sha256']}
            expected=dependencies_for_definition(definition)
            desired={(e.depends_on_subject_id,e.dependency_type):e.configuration for e in expected}
            actual={}
            for r in configured:
                key=(r['depends_on_subject_id'],r['dependency_type'])
                if key in actual:raise ValueError('Overlapping effective dependency records')
                actual[key]=_json(r['configuration_json'])
            if actual!=desired:raise ValueError('Approved definition and registered dependencies disagree')
            for e in sorted(expected,key=lambda e:(e.depends_on_subject_id,e.dependency_type)):
                parent=e.depends_on_subject_id
                if e.dependency_type=='benchmark':
                    component=next(c for c in definition['components'] if c['component_id']==parent)
                    edges.append({'subject_id':subject,'parent_id':parent,'kind':'benchmark',
                                  'configuration':e.configuration,'source_currency':component['source_currency'],
                                  'price_convention':component['price_convention']})
                else:
                    if definition['subject_type']=='filtered_portfolio':
                        if (parent not in records or records[parent]['subject_type']!='physical_account'
                                or records[parent]['base_currency']!=row['base_currency']):
                            raise ValueError('Filtered source must be a registered physical account')
                    else:
                        component=next(c for c in definition['components'] if c['component_id']==parent)
                        parent_kind=records.get(parent,{}).get('subject_type')
                        if ((component['component_type']=='account' and parent_kind!='physical_account')
                                or (component['component_type']=='portfolio' and parent_kind not in
                                    ('filtered_portfolio','composite_portfolio'))
                                or records.get(parent,{}).get('base_currency')!=component['source_currency']):
                            raise ValueError('Component type and parent registry disagree')
                    visit(parent)
                    edges.append({'subject_id':subject,'parent_id':parent,'kind':e.dependency_type,
                                  'configuration':e.configuration})
        elif kind=='physical_account':
            if current or configured:raise ValueError('Unexpected physical-account dependencies or calculation')
        else:raise ValueError('Unsupported financial lineage subject')
        visiting.remove(subject)
        nodes[subject]={'subject_id':subject,'subject_type':kind,'base_currency':row['base_currency'],
            'history_start_date':str(row['history_start_date']),'definition':identity}
        ordered.append(subject)
    for subject in sorted(roots):visit(subject)
    result={'version':VERSION,'tenant_id':tenant_id,'as_of':cutoff.isoformat(),
            'roots':sorted(roots),'order':ordered,'nodes':nodes,'edges':edges}
    # IDs and scopes included: reverting values does not hide a new definition.
    result['graph_sha256']=digest(result)
    return result


def _readonly(connection,tenant_id):
    context=connection.execute("SELECT app_tenant_id() AS tenant_id, "
        "current_setting('transaction_read_only') AS read_only, "
        "current_setting('transaction_isolation') AS isolation").fetchone()
    if (str(context['tenant_id'])!=str(tenant_id) or context['read_only']!='on'
            or context['isolation'] not in ('repeatable read','serializable')):
        raise ValueError('Tenant-scoped consistent READ ONLY transaction required')


def load_graph(connection,*,tenant_id,as_of):
    _readonly(connection,tenant_id);cutoff=_day(as_of).isoformat()
    def rows(query,args):return [dict(r) for r in connection.execute(query,args).fetchall()]
    subjects=rows('''SELECT tenant_id,subject_id,subject_type,lifecycle_status,base_currency,history_start_date
        FROM platform_subjects WHERE tenant_id=? ORDER BY subject_id''',(tenant_id,))
    definitions=rows('''SELECT tenant_id,subject_id,subject_type,calculation_kind,definition_id,definition_version,
        configuration_json,configuration_sha256,approval_status,effective_from,effective_to
        FROM subject_calculation_definitions WHERE tenant_id=? AND approval_status='approved'
        AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?) ORDER BY subject_id,definition_version''',
        (tenant_id,cutoff,cutoff))
    dependencies=rows('''SELECT tenant_id,subject_id,depends_on_subject_id,dependency_type,
        effective_from,effective_to,configuration_json FROM subject_dependencies WHERE tenant_id=?
        AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)
        ORDER BY subject_id,depends_on_subject_id,dependency_type,effective_from''',(tenant_id,cutoff,cutoff))
    roots=[r['subject_id'] for r in subjects if r['lifecycle_status']=='active'
           and r['subject_type']!='master_account' and _day(r['history_start_date'])<=_day(cutoff)]
    return graph(tenant_id=tenant_id,as_of=cutoff,subjects=subjects,definitions=definitions,
                 dependencies=dependencies,roots=roots)


def nav_observation(plan,subject,rows):
    """Finite, positive, uniquely dated base-currency producer NAV fingerprint."""
    node=plan['nodes'][subject];cutoff=_day(plan['as_of']);values=[];days=set()
    for raw in rows:
        r=dict(raw);day=_day(r['snapshot_date']);value=_number(r['total_text'])
        if (str(r['tenant_id'])!=plan['tenant_id'] or r['subject_id']!=subject
                or r['currency']!=node['base_currency'] or day>cutoff
                or day<_day(node['history_start_date']) or day in days
                or not value.is_finite() or value<=0):
            raise ValueError('Invalid or cross-account producer NAV observation')
        days.add(day);values.append({'date':day.isoformat(),'currency':r['currency'],'total_text':r['total_text']})
    if not days:raise ValueError('Producer NAV history missing')
    values.sort(key=lambda r:r['date'])
    return {'subject_id':subject,'through_date':max(days).isoformat(),'observations':len(values),
        'output_sha256':digest({'tenant_id':plan['tenant_id'],'subject_id':subject,'nav':values}),
        'coverage_verified':max(days)==cutoff}


def inspect_lineage(connection,*,tenant_id,as_of):
    """Connect real NAV outputs and benchmark dependencies, returning hashes only.

    Does not fingerprint positions/reference classifications/raw imports yet;
    cannot authorize a derived rebuild or certify all consumed financial inputs.
    """
    plan=load_graph(connection,tenant_id=tenant_id,as_of=as_of);observations={}
    for subject in plan['order']:
        node=plan['nodes'][subject]
        rows=connection.execute('''SELECT tenant_id,subject_id,snapshot_date,currency,total_text
            FROM subject_nav_series WHERE tenant_id=? AND subject_id=? AND currency=?
            AND snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date''',
            (tenant_id,subject,node['base_currency'],node['history_start_date'],plan['as_of'])).fetchall()
        observations[subject]=nav_observation(plan,subject,rows)
    markets={}
    for benchmark in sorted({e['parent_id'] for e in plan['edges'] if e['kind']=='benchmark'}):
        currencies={e['source_currency'] for e in plan['edges']
                    if e['kind']=='benchmark' and e['parent_id']==benchmark}
        if len(currencies)!=1:raise ValueError('Conflicting benchmark source currencies')
        currency=next(iter(currencies))
        expected=benchmark_session_on_or_before(benchmark,_day(plan['as_of']))
        rows=[dict(r) for r in connection.execute('''SELECT tenant_id,benchmark_id,observation_date,level,
            currency,provider,observation_status,quality_status FROM benchmark_prices WHERE tenant_id=?
            AND benchmark_id=? AND observation_date<=? AND quality_status IN ('pass','warn')
            AND provider='Yahoo Finance via yfinance' AND observation_status='provisional_close'
            ORDER BY observation_date,provider,observation_status''',(tenant_id,benchmark,plan['as_of'])).fetchall()]
        seen=set()
        for row in rows:
            value=_number(row['level'])
            if (str(row['tenant_id'])!=str(tenant_id) or row['benchmark_id']!=benchmark
                    or _day(row['observation_date'])>_day(plan['as_of']) or not value.is_finite() or value<=0
                    or row['provider']!='Yahoo Finance via yfinance' or row['observation_status']!='provisional_close'
                    or row['currency']!=currency or row['quality_status'] not in ('pass','warn')
                    or _day(row['observation_date']) in seen):
                raise ValueError('Invalid market lineage observation')
            seen.add(_day(row['observation_date']));row['tenant_id']=str(row['tenant_id'])
            row['observation_date']=_day(row['observation_date']).isoformat();row['level']=str(value)
        markets[benchmark]={'expected_date':expected.isoformat(),'observations':len(rows),
            'output_sha256':digest(rows),'coverage_verified':bool(rows) and _day(rows[-1]['observation_date'])==expected}
    roots={};lineage_hashes={}
    def manifest(subject):
        if subject in lineage_hashes:return lineage_hashes[subject]
        parents=[]
        for edge in plan['edges']:
            if edge['subject_id']==subject:
                value=digest(markets[edge['parent_id']]) if edge['kind']=='benchmark' else manifest(edge['parent_id'])
                parents.append(dict(edge,parent_lineage_sha256=value))
        lineage_hashes[subject]=digest({'version':VERSION,'tenant_id':plan['tenant_id'],'as_of':plan['as_of'],
            'node':plan['nodes'][subject],'output':observations[subject],'parents':parents})
        return lineage_hashes[subject]
    for subject in plan['roots']:roots[subject]=manifest(subject)
    return {'version':VERSION,'tenant_id':str(tenant_id),'as_of':plan['as_of'],
        'calendar_version':CALENDAR_VERSION,'graph_sha256':plan['graph_sha256'],'order':plan['order'],
        'edges':plan['edges'],'producer_outputs':observations,'market_outputs':markets,'root_lineage_sha256':roots,
        'registry_verified':True,'observed_coverage_verified':all(o['coverage_verified'] for o in
            (*observations.values(),*markets.values())),
        'source_completeness_verified':False,'all_consumed_inputs_verified':False,'execution_authorized':False}
