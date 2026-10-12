import copy,unittest,json,sqlite3
from pathlib import Path
from unittest.mock import Mock,patch
from ibkr_etl.alpha_world_t_reporting import enrich,COEFFICIENTS
from test_alpha_world_t_analytics import snapshot

def fixture():
    core=snapshot();rows=[]
    for point in core['performance']['series']:
        for key,coefficient in COEFFICIENTS.items():
            value=coefficient*100 if key!='constant-cash' else 58
            if key=='U16088606':value+=point['nav']-100
            level=value/coefficient
            rows.append({'portfolio_id':'alpha-world-t','snapshot_date':point['date'],'component_id':key,
                'base_currency':'HKD','coefficient':coefficient,'source_value':level,'fx_rate_to_base':1,
                'base_value':level,'normalized_value':level,'contribution':value,
                'direction':'cash' if key=='constant-cash' else 'long' if coefficient>0 else 'short'})
    return core,rows

class TReportingTests(unittest.TestCase):
    def test_pure_and_exact_component_bridge(self):
        core,rows=fixture();before=copy.deepcopy((core,rows));result=enrich(core,rows)
        self.assertEqual((core,rows),before)
        self.assertFalse(result['publication_allowed'])
        self.assertIn('risk:crisis_replay_unavailable',result['publication_blockers'])
        self.assertEqual(len(result['holdings']['holdings']),5)
        self.assertAlmostEqual(sum(r['contribution'] for r in result['monthly_reporting']['security_contributions']),
                               core['performance']['series'][-1]['nav']/100-1)
        self.assertEqual(result['monthly_reporting']['period_start'],'2025-01-20')

    def test_component_mutations_and_missing_dates_rejected(self):
        mutations=[lambda r:r.pop(),lambda r:r.append(copy.deepcopy(r[0])),
            lambda r:r[0].update(base_currency='USD'),lambda r:r[0].update(portfolio_id='alpha-world-ms'),
            lambda r:r[0].update(coefficient=.9),lambda r:r[0].update(source_value=1),
            lambda r:r[0].update(contribution=float('nan')),lambda r:r[0].update(direction='short'),
            lambda r:r[0].update(snapshot_date='2025-01-19')]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                core,rows=fixture();mutation(rows)
                with self.assertRaises(ValueError):enrich(core,rows)

    def test_components_must_reconcile_to_each_day_not_only_latest(self):
        core,rows=fixture()
        for key in ('source_value','base_value','normalized_value'):rows[0][key]+=1
        rows[0]['contribution']+=.91
        with self.assertRaisesRegex(ValueError,'reconcile'):enrich(core,rows)

    def test_month_end_snapshot_uses_t_profile_and_preserves_internal_limit(self):
        from ibkr_etl.reporting_snapshot import _collect_tool_results,assemble_month_end_payload,ReportingSnapshotError
        root=Path(__file__).resolve().parents[1]
        history=root/'.work/alpha-world-t-onboarding-20261008/history-v1'
        source=root/'.work/alpha-t-internal-crisis-20261009-v1/2026-09-30.json'
        if not source.exists():self.skipTest('Private qualification fixture unavailable')
        old=json.loads(source.read_bytes());core=json.loads((history/'snapshots/2026-09-30.json').read_bytes())
        c=sqlite3.connect((history/'qualification.sqlite').resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        try:rows=c.execute("SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-t' AND snapshot_date<=?",(core['data_as_of'],)).fetchall()
        finally:c.close()
        enriched=enrich(core,rows,old['risk']['crisis_replay']);db=Mock()
        with patch('ibkr_etl.alpha_world_t_reporting.load',return_value=enriched) as loader:
            tools=_collect_tool_results(db,tenant_id='tenant',subject_id='alpha-world-t',period_end='2026-09-30',reporting_currency='HKD')
            loader.assert_called_once_with(db,'2026-09-30',tenant_id='tenant')
            db.execute.assert_not_called()
        payload,quality,allowed,blockers=assemble_month_end_payload(subject_id='alpha-world-t',
            period_start='2025-01-20',period_end='2026-09-30',reporting_currency='HKD',tool_results=tools)
        self.assertEqual(payload['risk']['metrics'],enriched['risk']['metrics'])
        self.assertEqual(payload['methodology_identity'],core['methodology_identity'])
        self.assertEqual(quality,'warn');self.assertFalse(allowed)
        self.assertTrue(blockers);self.assertNotIn('risk:crisis_replay_unavailable',blockers)
        with self.assertRaises(ReportingSnapshotError):
            _collect_tool_results(db,tenant_id='tenant',subject_id='alpha-world-t',period_end='2026-09-30',reporting_currency='USD')
        broken=copy.deepcopy(tools);broken['risk']['data']['metrics']['total_return']=.9
        with self.assertRaises(ReportingSnapshotError):
            assemble_month_end_payload(subject_id='alpha-world-t',period_start='2025-01-20',period_end='2026-09-30',reporting_currency='HKD',tool_results=broken)

if __name__=='__main__':unittest.main()
