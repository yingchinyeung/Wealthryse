"""Dependency-free tests of release glue scope and immutable evidence guards."""
import ast
import hashlib
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]


class CutoverControlTests(unittest.TestCase):
    def functions(self):
        tree=ast.parse((ROOT/'scripts/apply_alpha_ms_hkd_cutover.py').read_text(encoding='utf-8'))
        names={'scope','backup','canonical','sha','data_hashes'}
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        ns={'json':json,'hashlib':hashlib,'TENANT':'owner-tenant',
            'TABLES':('platform_subjects','subject_calculation_definitions','derived_portfolios','reporting_snapshots',
                'risk_free_rates','derived_portfolio_nav','synthetic_portfolio_components','risk_analytics_snapshots')}
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'qualified-cutover-functions','exec'),ns)
        return ns

    def test_every_backup_query_has_exact_tenant_parameter(self):
        ns=self.functions();calls=[]
        class Fake:
            def execute(self,sql,parameters=None):
                calls.append((sql,parameters))
                if sql.count('?')!=1 or parameters!=('owner-tenant',):raise AssertionError('Unbound tenant query')
                return []
        ns['backup'](Fake())
        self.assertEqual(len(calls),len(ns['TABLES']))
        self.assertTrue(all('alpha-world-ms' in sql or 'HKAB-HIBOR-1M' in sql for sql,_ in calls))

    def test_backup_fingerprints_ignore_row_order_not_values(self):
        f=self.functions()['data_hashes']
        a={'nav':[{'date':'a','value':'1.00000000001'},{'date':'b','value':'2'}]}
        b={'nav':[dict(r) for r in reversed(a['nav'])]}
        self.assertEqual(f(a),f(b))
        b['nav'][0]['value']='2.00000000001'
        self.assertNotEqual(f(a),f(b))

    def test_mutation_allowlist_excludes_every_broker_table(self):
        text=(ROOT/'scripts/apply_alpha_ms_hkd_cutover.py').read_text(encoding='utf-8')
        tree=ast.parse(text)
        namespace={}
        for node in tree.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in ('MUTABLE','TABLES') for t in node.targets):
                exec(compile(ast.Module(body=[node],type_ignores=[]),'cutover-allowlist','exec'),namespace)
        for table in ('nav_snapshots','position_snapshots','fx_rates','cash_transactions','raw_documents'):
            self.assertNotIn(table,namespace['TABLES'])
        self.assertIn("def commit(self):pass",text)
        self.assertIn("raw.commit()",text)


if __name__=='__main__':unittest.main()
