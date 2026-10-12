"""Offline fresh qualification after protected native containerId failure."""
import contextlib
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import qualify_owner_task_aca_container_identity as c
import test_owner_task_aca_readiness_controller as b
from test_owner_task_aca_container_identity import IDENTIFIER

CURRENT=c.JOB+'-idpassx'


def replicas(empty=False,name=CURRENT):
    value=json.loads(json.dumps(b.replicas(empty)).replace(b.CURRENT,name))
    if not empty: value['value'][0]['properties']['containers'][0]['containerId']=IDENTIFIER
    return value


def context():
    previous=b.context(); names=previous['prior']['prior_executions']+[b.CURRENT]
    prior=dict(pf=previous['prior']['pf'],sources=previous['sources'],prior_executions=names,
        prior_raw_executions={name:b.b.execution(terminal=True,name=name) for name in names},
        receipts={name:dict(sha256=pin[0],version_id=pin[1]) for name,pin in c.PINS.items()})
    value=dict(previous,operation_id=c.OPERATION_ID,prior=prior,
        sources=dict(previous['sources'],**{name:'a'*64 for name in c.ADDITIONS}))
    value['live']=copy.deepcopy(previous['live'])
    value['live']['prior_execution_readbacks']=copy.deepcopy(prior['prior_raw_executions'])
    value['live']['executions']={'value':list(prior['prior_raw_executions'].values())}
    return value


class IdentityControllerTests(b.b.OfflineTestCase):
    def test_exact_four_source_additions_preserve_all739_hashes(self):
        p=context(); c.validate_sources(p['prior']['sources'],p['sources']);self.assertEqual(743,len(p['sources']))
        for change in ('old','missing','extra'):
            value=copy.deepcopy(p['sources'])
            if change=='old': value[next(iter(p['prior']['sources']))]='f'*64
            elif change=='missing': value.pop(next(iter(c.ADDITIONS)))
            else: value['extra.py']='f'*64
            with self.assertRaises(ValueError): c.validate_sources(p['prior']['sources'],value)

    def test_complete_inventory_requires_three_prior_and_one_distinct_current(self):
        p=context()['prior']; value={'value':list(p['prior_raw_executions'].values())}
        c.check_inventory(value,p['prior_executions'])
        value['value'].append(b.b.execution(name=CURRENT));c.check_inventory(value,p['prior_executions'],CURRENT)
        for name in p['prior_executions']:
            with self.assertRaises(ValueError): c.check_inventory(value,p['prior_executions'],name)
        with self.assertRaises(ValueError): c.check_inventory({'value':value['value'][:-1]},p['prior_executions'],CURRENT)
        with patch.object(c.old,'get',return_value={'value':value['value']+[{}]}),self.assertRaises(ValueError): c.inventory(Mock())

    def test_capture_waits_for_empty_metadata_and_preserves_ready_container_identity(self):
        running=b.b.execution(name=CURRENT); record=Mock();sleep=Mock()
        with patch.object(c.old,'get',side_effect=[running,replicas(True),running,replicas(),running]):
            result=c.capture(Mock(),context()['prior']['pf'],CURRENT,record,monotonic=lambda:0,sleep=sleep)
        sleep.assert_called_once_with(1)
        self.assertEqual(IDENTIFIER,result['replica']['properties']['containers'][0]['containerId'])
        self.assertEqual(5,record.call_count)

    def test_capture_requires_real_after_observation_and_never_fills_it(self):
        running=b.b.execution(name=CURRENT);after=copy.deepcopy(running);after['properties']['status']='Processing'
        with patch.object(c.old,'get',side_effect=[running,replicas(),after]),self.assertRaises(ValueError):
            c.capture(Mock(),context()['prior']['pf'],CURRENT,Mock(),monotonic=lambda:0,sleep=Mock())

    def test_intent_is_detached_and_binds_all_consumed_attempts(self):
        p=context();before=copy.deepcopy(p); value=c.intent_value(b.b.protected(p,'preflight.json'),p)
        self.assertEqual(3,len(value['prior_executions']));self.assertFalse(value['automatic_retry'])
        value['prior_executions'].append(CURRENT);value['body']['containers'][0]['image']='foreign';self.assertEqual(before,p)

    def test_protected_prior_diagnosis_is_validated_without_requalifying(self):
        previous=b.context();ack=dict(status='new_qualification_execution_acknowledged',attempt_consumed=True,
            **b.b.ack_value(name=b.CURRENT));ackraw=b.b.protected(ack,'start-ack.json')
        observations=[dict(kind='start_ack',received_at_utc='2026-10-04T23:50:26Z',raw=b.b.ack_value(name=b.CURRENT))]
        for index in range(13):
            observations.append(dict(kind='execution_before',received_at_utc='2026-10-04T23:50:27Z',raw=b.b.execution(name=b.CURRENT)))
            observations.append(dict(kind='replicas',received_at_utc='2026-10-04T23:50:27Z',raw=replicas(index!=12,name=b.CURRENT)))
        diagnosis=dict(status='native_capture_unqualified_inspect_only',operation_id=c.stopped.OPERATION_ID,
            failure_code='native_replica_invalid',start_ack_receipt=ackraw['protected_receipt'],attempt_consumed=True,
            acceptance_recorded=False,automatic_retry=False,sql_calls=0,model_calls=0,observations=observations,
            **{k:False for k in c.FALSE_FLAGS})
        values={'preflight.json':previous,'start-ack.json':ack,'capture-diagnostic.json':diagnosis}
        def load(name): return (ackraw,ack) if name=='start-ack.json' else (b.b.protected(values[name],name),values[name])
        with patch.object(c.stopped,'load',side_effect=load),patch.object(c.old,'validate_receipt') as pins, \
                patch.object(c.stopped,'chain',return_value=(ackraw,b.CURRENT)), \
                patch.object(c.old.previous,'sources',return_value=context()['sources']), \
                patch.object(c.old,'arm',return_value=Mock()),patch.object(c.old,'get',return_value=b.b.execution(terminal=True,name=b.CURRENT)), \
                patch.object(c,'save') as save,patch.object(c,'reserve') as reserve:
            result=c.history();self.assertEqual(3,len(result['prior_executions']));self.assertEqual(3,pins.call_count)
            for change in ('accepted','order','identifier','time'):
                before=copy.deepcopy(diagnosis)
                if change=='accepted': diagnosis['acceptance_recorded']=True
                elif change=='order': observations[1]['kind']='execution_after'
                elif change=='identifier': observations[-1]['raw']['value'][0]['properties']['containers'][0]['containerId']='containerd://'+'a'*32
                else: observations[-1]['received_at_utc']='2026-10-04T23:55:27Z'
                with self.assertRaises(ValueError): c.history()
                diagnosis.clear();diagnosis.update(before);observations=diagnosis['observations']
        save.assert_not_called();reserve.assert_not_called()

    def test_collect_and_independent_verify_preserve_raw_identifier_and_never_resave(self):
        p=context(); raw=b.b.protected(p,'preflight.json');ack=b.b.protected({'status':'ack'},'start-ack.json')
        captured=dict(status='native_running_replica_observed',operation_id=c.OPERATION_ID,start_ack_receipt=ack['protected_receipt'],
            binding=dict(raw_execution=b.b.execution(name=CURRENT),raw_replicas=replicas(),replica=replicas()['value'][0]))
        terminal=b.b.execution(terminal=True,name=CURRENT);live=copy.deepcopy(p['live']);live['executions']['value'].append(terminal)
        row=json.loads(json.dumps(b.b.console_row()).replace(b.b.REPLICA,CURRENT+'-abc12'));saved={}
        query=Mock(side_effect=lambda _:b.b.console_response([row]))
        def save(value,name): saved[name]=copy.deepcopy(value);return b.b.protected(value,name)
        def load(name):
            value=captured if name=='replica-binding.json' else saved[name]
            return b.b.protected(value,name),copy.deepcopy(value)
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            for target,attribute,replacement in ((c,'FOLDER',Path(folder)),(c,'prepared',Mock(return_value=(raw,p))),
                    (c,'chain',Mock(return_value=(ack,CURRENT))),(c,'load',Mock(side_effect=load)),
                    (c.old,'arm',Mock(return_value=Mock())),(c.old,'get',Mock(return_value=terminal)),
                    (c,'guards',Mock(return_value=live)),(c.old.n,'resource_guards',Mock(return_value=p['prior']['pf']['resource_guards'])),
                    (c.old.previous,'sources',Mock(return_value=p['sources'])),
                    (c.old,'LogsTransport',Mock(return_value=SimpleNamespace(call=query))),
                    (c.old,'storage',Mock(return_value=Mock())),(c.old,'blob_absent',Mock())):
                stack.enter_context(patch.object(target,attribute,replacement))
            saver=stack.enter_context(patch.object(c,'save',side_effect=save))
            result=c.collect();self.assertEqual('independent_native_runtime_readback_verified',c.collect(True)['status'])
            self.assertEqual(4,len(result['complete_execution_inventory']['value']));self.assertFalse(result['original_stopped_attempt_requalified'])
            self.assertEqual(0,result['sql_calls']);self.assertEqual(0,result['model_calls']);saver.assert_called_once()
            saved['acceptance.json']['original_stopped_attempt_requalified']=True
            with self.assertRaises(ValueError): c.collect(True)
            saver.assert_called_once()

    def test_consumed_capture_and_collection_stops_deny_without_cloud_effects(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(c,'FOLDER',Path(folder)),patch.object(c,'collect_impl') as collect:
            (Path(folder)/'capture-diagnostic.json').touch()
            with self.assertRaises(ValueError): c.collect()
            collect.assert_not_called()


if __name__=='__main__': unittest.main()
