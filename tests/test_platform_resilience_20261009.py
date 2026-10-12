"""Offline regression for narrowly scoped resilience drift guards."""
import ast
import copy
from pathlib import Path
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'scripts/upgrade_platform_resilience_v2_20261009.py'
tree=ast.parse(SOURCE.read_text())
namespace={'copy':copy}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='normalized'],type_ignores=[]),str(SOURCE),'exec'),namespace)
normalized=namespace['normalized']

class DriftTests(unittest.TestCase):
    def setUp(self):
        self.value={'id':'fixed','location':'eastasia','sku':{'name':'Basic'},'tags':{'owner':'unchanged'},
                    'properties':{'provisioningState':'Succeeded','state':'Ready','publicNetworkAccess':'Enabled',
                                  'adminUserEnabled':False,'networkRuleSet':{'defaultAction':'Allow'},
                                  'backup':{'backupRetentionDays':14,'earliestRestoreDate':'old','geoRedundantBackup':'Disabled'},
                                  'highAvailability':{'mode':'ZoneRedundant'},'identity':{'principalId':'unchanged'}}}
    def test_registry_tier_only_is_permitted(self):
        after=copy.deepcopy(self.value);after['sku']={'name':'Premium'}
        self.assertEqual(normalized(after,'registry'),normalized(self.value,'registry'))
        self.assertEqual(self.value['sku']['name'],'Basic')
    def test_backup_retention_and_service_timestamp_only(self):
        after=copy.deepcopy(self.value);after['properties']['backup'].update(backupRetentionDays=35,earliestRestoreDate='new')
        self.assertEqual(normalized(after,'database'),normalized(self.value,'database'))
    def test_all_other_security_settings_remain_guarded(self):
        for key in ('publicNetworkAccess','adminUserEnabled','networkRuleSet','identity','highAvailability'):
            for kind in ('registry','database'):
                with self.subTest(key=key,kind=kind):
                    after=copy.deepcopy(self.value);after['properties'][key]='changed'
                    self.assertNotEqual(normalized(after,kind),normalized(self.value,kind))
    def test_region_and_tags_remain_guarded(self):
        for key in ('location','tags','id'):
            after=copy.deepcopy(self.value);after[key]='changed'
            self.assertNotEqual(normalized(after,'registry'),normalized(self.value,'registry'))
    def test_no_georedundancy_or_database_sku_change(self):
        after=copy.deepcopy(self.value);after['properties']['backup']['geoRedundantBackup']='Enabled'
        self.assertNotEqual(normalized(after,'database'),normalized(self.value,'database'))
    def test_only_equivalent_azure_default_is_normalized(self):
        before=copy.deepcopy(self.value);before['properties'].pop('networkRuleSet')
        after=copy.deepcopy(before);after['properties']['networkRuleSet']={'defaultAction':'Allow','ipRules':[]}
        self.assertEqual(normalized(before,'registry'),normalized(after,'registry'))
        after['properties']['networkRuleSet']['defaultAction']='Deny'
        self.assertNotEqual(normalized(before,'registry'),normalized(after,'registry'))
        after['properties']['networkRuleSet']={'defaultAction':'Allow','ipRules':[{'value':'1.2.3.4','action':'Allow'}]}
        self.assertNotEqual(normalized(before,'registry'),normalized(after,'registry'))
        after=copy.deepcopy(self.value);after['sku']={'name':'large'}
        self.assertNotEqual(normalized(after,'database'),normalized(self.value,'database'))

if __name__=='__main__':unittest.main()
