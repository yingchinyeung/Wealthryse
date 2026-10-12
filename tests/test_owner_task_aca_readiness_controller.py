"""Offline regression for new qualification after retained startup failure."""
import copy
import contextlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import qualify_owner_task_aca_readiness as c
import test_owner_task_aca_native_controller as b
from test_owner_task_aca_readiness import empty_replica

CURRENT = c.JOB + '-rdypass'


def replicas(empty=False):
    value = b.replicas()
    value = json.loads(json.dumps(value).replace(b.CURRENT, CURRENT))
    if empty:
        value['value'][0]['properties'].update(containers=[], runningState='NotRunning', runningStateDetails='')
    return value


def context():
    previous = b.new_preflight(); names = [b.PRIOR, b.CURRENT]
    prior = dict(pf=b.prior_context()['pf'], sources=previous['sources'], prior_executions=names,
        prior_raw_executions={name:b.execution(terminal=True,name=name) for name in names},
        receipts={name:dict(sha256=pin[0],version_id=pin[1]) for name,pin in c.PINS.items()})
    value = dict(previous, operation_id=c.OPERATION_ID, prior=prior,
        sources=dict(previous['sources'], **{name:'e'*64 for name in c.ADDITIONS}))
    live = b.live_context(current=True); live.pop('prior_execution_readback')
    live['prior_execution_readbacks'] = copy.deepcopy(prior['prior_raw_executions'])
    value['live'] = live
    return value


class ReadinessControllerTests(b.OfflineTestCase):
    def test_source_extension_is_exactly_four_additions_with_all735_preserved(self):
        p = context(); c.validate_sources(p['prior']['sources'], p['sources'])
        self.assertEqual(739,len(p['sources']))
        for change in ('prior','missing','extra','type'):
            value=copy.deepcopy(p['sources'])
            if change=='prior': value[next(iter(p['prior']['sources']))]='f'*64
            elif change=='missing': value.pop(next(iter(c.ADDITIONS)))
            elif change=='extra': value['extra.py']='f'*64
            else: value[next(iter(c.ADDITIONS))]=False
            with self.subTest(change=change),self.assertRaises(ValueError): c.validate_sources(p['prior']['sources'],value)

    def test_complete_inventory_retains_both_stopped_executions_and_one_distinct_new_run(self):
        priors=context()['prior']['prior_executions']; rows=[b.execution(terminal=True,name=n) for n in priors]
        c.check_inventory({'value':rows},priors)
        value={'value':rows+[b.execution(name=CURRENT)]}; c.check_inventory(value,priors,CURRENT)
        for current in priors:
            with self.assertRaises(ValueError): c.check_inventory(value,priors,current)
        for changed in ({'value':value['value'][1:]},{'value':value['value']+[b.execution(name=c.JOB+'-unknown')]},
                {'value':value['value'],'nextLink':'next'}, {'value':[rows[0],rows[0],value['value'][2]]}):
            with self.assertRaises(ValueError): c.check_inventory(changed,priors,CURRENT)
        with patch.object(c.old,'get',return_value={'value':value['value']*2}),self.assertRaises(ValueError): c.inventory(Mock())

    def test_intent_is_detached_and_binds_both_consumed_executions(self):
        p=context(); before=copy.deepcopy(p); raw=b.protected(p,'preflight.json')
        value=c.intent_value(raw,p)
        self.assertEqual(p['prior']['prior_executions'],value['prior_executions'])
        self.assertEqual(c.JOB_ID+'/start',value['path']); self.assertIs(value['automatic_retry'],False)
        value['prior_executions'].append(CURRENT); value['body']['containers'][0]['image']='foreign'
        self.assertEqual(before,p)

    def test_empty_startup_poll_then_real_ready_capture_with_no_dispatch(self):
        p=context()['prior']['pf']; running=b.execution(name=CURRENT); record=Mock(); sleep=Mock()
        values=[running,replicas(True),running,replicas(),running]
        with patch.object(c.old,'get',side_effect=values),patch.object(c,'reserve') as reserve:
            value=c.capture(Mock(),p,CURRENT,record,monotonic=lambda:0,sleep=sleep)
        sleep.assert_called_once_with(1); reserve.assert_not_called()
        self.assertEqual(replicas()['value'][0],value['replica'])
        self.assertEqual(['execution_before','replicas','execution_before','replicas','execution_after'],
            [call.args[0] for call in record.call_args_list])

    def test_stopped_history_pins_exact_diagnosis_and_never_records_acceptance(self):
        oldpf=b.new_preflight(); names=[b.PRIOR,b.CURRENT]
        ack=dict(status='new_qualification_execution_acknowledged',attempt_consumed=True,**b.ack_value())
        ackraw=b.protected(ack,'start-ack.json')
        empty=b.replicas(); empty['value'][0]['properties'].update(containers=[],runningState='NotRunning',runningStateDetails='')
        diagnosis=dict(status='native_capture_unqualified_inspect_only',operation_id=c.stopped.OPERATION_ID,
            failure_code='native_replica_invalid',start_ack_receipt=ackraw['protected_receipt'],attempt_consumed=True,
            acceptance_recorded=False,automatic_retry=False,sql_calls=0,model_calls=0,
            observations=[dict(kind='start_ack',raw=b.ack_value()),dict(kind='execution_before',raw=b.execution()),
                dict(kind='replicas',raw=empty)],**{k:False for k in c.FALSE_FLAGS})
        records={'preflight.json':oldpf,'start-ack.json':ack,'capture-diagnostic.json':diagnosis}
        before=copy.deepcopy(records)
        def load(name): return (ackraw,ack) if name=='start-ack.json' else (b.protected(records[name],name),records[name])
        with patch.object(c.stopped,'load',side_effect=load),patch.object(c.old,'validate_receipt') as receipts, \
                patch.object(c.stopped,'chain',return_value=(ackraw,b.CURRENT)), \
                patch.object(c.old.previous,'sources',return_value=context()['sources']), \
                patch.object(c.old,'arm',return_value=Mock()),patch.object(c.old,'get',return_value=b.execution(terminal=True)), \
                patch.object(c,'save') as save,patch.object(c,'reserve') as reserve:
            value=c.history()
            self.assertEqual(names,value['prior_executions']); self.assertEqual(3,receipts.call_count)
            diagnosis['acceptance_recorded']=True
            with self.assertRaises(ValueError): c.history()
        diagnosis['acceptance_recorded']=False
        self.assertEqual(before,records); save.assert_not_called();reserve.assert_not_called()

    def test_guards_read_every_original_execution_and_reject_changed_prior(self):
        p=context()['prior']; base=p['pf']; live=context()['live']; identity=base['identity']
        def get(client,path,api):
            if path==c.JOB_ID: return live['job']
            if path==c.old.transport.ASSIGNMENT_ID: return live['assignment']
            return p['prior_raw_executions'][path.rsplit('/',1)[-1]]
        with patch.object(c.old,'arm',return_value=Mock()),patch.object(c.old.c,'identity_observation',return_value=identity), \
                patch.object(c.old,'environment',return_value={}),patch.object(c.old,'get',side_effect=get), \
                patch.object(c.native,'validate_job_metadata'),patch.object(c.old,'roles',return_value=live['roles']), \
                patch.object(c,'inventory',return_value=live['executions']):
            value=c.guards(base,p['prior_executions'])
            self.assertEqual(p['prior_raw_executions'],value['prior_execution_readbacks'])
            p['prior_raw_executions'][b.CURRENT]['properties']['status']='Failed'
            with self.assertRaises(ValueError): c.guards(base,p['prior_executions'])

    def test_chain_rejects_reuse_of_either_consumed_execution(self):
        p=context(); raw=b.protected(p,'preflight.json'); expected=c.intent_value(raw,p)
        iraw=b.protected(dict(expected,status='reserved_single_qualification',reservation_receipt={}), 'start-intent.json')
        for name in (CURRENT,b.PRIOR,b.CURRENT):
            ack=dict(status='new_qualification_execution_acknowledged',operation_id=c.OPERATION_ID,
                intent_receipt=iraw['protected_receipt'],attempt_consumed=True,automatic_retry=False,**b.ack_value(name=name))
            def load(filename): return (iraw,{k:v for k,v in iraw.items() if k!='protected_receipt'}) if filename=='start-intent.json' else (b.protected(ack,'start-ack.json'),ack)
            with patch.object(c,'load',side_effect=load),patch.object(c,'reservation_readback'):
                if name==CURRENT: self.assertEqual(CURRENT,c.chain(raw,p)[1])
                else:
                    with self.assertRaises(ValueError): c.chain(raw,p)

    def test_collect_then_fresh_verify_binds_three_executions_and_never_resaves(self):
        p=context(); raw=b.protected(p,'preflight.json'); ack=b.protected({'status':'ack'},'start-ack.json')
        captured=dict(status='native_running_replica_observed',operation_id=c.OPERATION_ID,
            start_ack_receipt=ack['protected_receipt'],binding=dict(raw_execution=b.execution(name=CURRENT),
                raw_replicas=replicas(),replica=replicas()['value'][0]))
        terminal=b.execution(terminal=True,name=CURRENT); live=copy.deepcopy(p['live'])
        live['executions']['value'].append(terminal)
        row=json.loads(json.dumps(b.console_row()).replace(b.REPLICA,CURRENT+'-abc12'))
        query=Mock(side_effect=lambda _:b.console_response([row])); saved={}
        def save(value,name): saved[name]=copy.deepcopy(value);return b.protected(value,name)
        def load(name):
            value=captured if name=='replica-binding.json' else saved[name]
            return b.protected(value,name),copy.deepcopy(value)
        with tempfile.TemporaryDirectory() as folder,contextlib.ExitStack() as stack:
            for target,replacement in ((c,'FOLDER'),): stack.enter_context(patch.object(target,replacement,Path(folder)))
            stack.enter_context(patch.object(c,'prepared',return_value=(raw,p)))
            stack.enter_context(patch.object(c,'chain',return_value=(ack,CURRENT)))
            stack.enter_context(patch.object(c,'load',side_effect=load)); save_mock=stack.enter_context(patch.object(c,'save',side_effect=save))
            stack.enter_context(patch.object(c.old,'arm',return_value=Mock()))
            stack.enter_context(patch.object(c.old,'get',return_value=terminal))
            stack.enter_context(patch.object(c,'guards',return_value=live))
            stack.enter_context(patch.object(c.old.n,'resource_guards',return_value=p['prior']['pf']['resource_guards']))
            stack.enter_context(patch.object(c.old.previous,'sources',return_value=p['sources']))
            stack.enter_context(patch.object(c.old,'LogsTransport',return_value=SimpleNamespace(call=query)))
            stack.enter_context(patch.object(c.old,'storage',return_value=Mock()));stack.enter_context(patch.object(c.old,'blob_absent'))
            value=c.collect(); verified=c.collect(True)
            self.assertEqual(p['prior']['prior_raw_executions'],value['raw_prior_executions'])
            self.assertEqual(3,len(value['complete_execution_inventory']['value']))
            self.assertEqual('independent_native_runtime_readback_verified',verified['status']);save_mock.assert_called_once()
            saved['acceptance.json']['original_stopped_attempt_requalified']=True
            with self.assertRaises(ValueError): c.collect(True)
            save_mock.assert_called_once()


if __name__=='__main__': unittest.main()
