"""Offline native container identity compatibility, not live acceptance."""
import copy
import importlib
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import owner_task_aca_container_identity as c
from test_owner_task_aca_native import OfflineTestCase, nested_replica
from test_owner_task_aca_runtime_named import EXECUTION, console_row, plan_pf, observed
from test_owner_task_aca_readiness import empty_replica

IDENTIFIER = 'containerd://70f8b8632b314686bb149f3bae439345'


def identified_replica():
    value = nested_replica()
    value['value'][0]['properties']['containers'][0]['containerId'] = IDENTIFIER
    return value


class ContainerIdentityTests(OfflineTestCase):
    def test_import_inert_and_no_effect_entrypoint(self):
        with patch('os.getenv',side_effect=AssertionError('environment access')):
            importlib.reload(c)
        for name in ('start','install','main','get_token','connect','dispatch'):
            self.assertFalse(hasattr(c,name))

    def test_known_identifiers_preserve_raw_and_are_not_image_digest_proof(self):
        for identifier in (IDENTIFIER,'containerd://'+'a'*64):
            value=identified_replica(); value['value'][0]['properties']['containers'][0]['containerId']=identifier
            before=copy.deepcopy(value)
            self.assertEqual('ready',c.classify_replica(value,EXECUTION))
            self.assertEqual(before['value'][0],c.validate_replica(value,EXECUTION))
            self.assertEqual(before,value)

    def test_invalid_optional_identifier_is_not_ignored_or_coerced(self):
        for identifier in (None,True,32,'','containerd://','containerd://'+'a'*33,
                'containerd://'+'A'*32,'docker://'+'a'*64,IDENTIFIER+'?token=x',IDENTIFIER+'\n'):
            value=identified_replica(); value['value'][0]['properties']['containers'][0]['containerId']=identifier
            with self.subTest(identifier=identifier),self.assertRaises(ValueError):
                c.classify_replica(value,EXECUTION)

    def test_optional_omission_and_empty_metadata_retain_predecessor_rules(self):
        self.assertEqual('ready',c.classify_replica(nested_replica(),EXECUTION))
        self.assertEqual('pending',c.classify_replica(empty_replica(),EXECUTION))
        with self.assertRaises(c.ReplicaPending): c.validate_replica(empty_replica(),EXECUTION)

    def test_all_other_scope_endpoints_and_readiness_constraints_still_apply(self):
        for change in ('restart','name','endpoint','extra','init','scope','time','duplicate','next','ready'):
            value=identified_replica(); row=value['value'][0]; props=row['properties']; container=props['containers'][0]
            if change=='restart': container['restartCount']=1
            elif change=='name': container['name']='foreign'
            elif change=='endpoint': container['execEndpoint']+='?token=unsafe'
            elif change=='extra': container['arbitraryMetadata']='x'
            elif change=='init': props['initContainers']=[{'name':'other'}]
            elif change=='scope': row['id']+='foreign'
            elif change=='time': props['createdTime']='2026-10-04T23:50:26'
            elif change=='duplicate': value['value'].append(copy.deepcopy(row))
            elif change=='next': value['nextLink']='next'
            else: container['started']=False
            with self.subTest(change=change),self.assertRaises(ValueError): c.classify_replica(value,EXECUTION)

    def test_pending_and_terminal_identifiers_never_supply_running_evidence(self):
        value=identified_replica(); props=value['value'][0]['properties']; container=props['containers'][0]
        props['runningState']='NotRunning'; container.update(ready=False,started=False,runningState='Waiting')
        self.assertEqual('pending',c.classify_replica(value,EXECUTION))
        with self.assertRaises(c.ReplicaPending): c.validate_replica(value,EXECUTION)
        container['runningState']='Terminated'
        with self.assertRaises(ValueError): c.classify_replica(value,EXECUTION)

    def test_log_ownership_preserves_identifier_and_full_event_binding(self):
        replica=identified_replica()['value'][0]; rows=[console_row()]; pf=plan_pf(); before=copy.deepcopy((replica,rows,pf))
        self.assertEqual(observed(),c.validate_log_binding(rows,replica,pf))
        self.assertEqual(before,(replica,rows,pf))
        rows.append(copy.deepcopy(rows[0]))
        with self.assertRaises(ValueError): c.validate_log_binding(rows,replica,pf)

    def test_log_binding_rejects_invalid_identifier_unready_or_foreign_container(self):
        for change in ('identifier','ready','container'):
            replica=identified_replica()['value'][0]; container=replica['properties']['containers'][0]
            if change=='identifier': container['containerId']='unregistered'
            elif change=='ready': container['ready']=False
            else: container['name']='foreign'
            with self.subTest(change=change),self.assertRaises(ValueError): c.validate_log_binding([console_row()],replica,plan_pf())


if __name__=='__main__': unittest.main()
