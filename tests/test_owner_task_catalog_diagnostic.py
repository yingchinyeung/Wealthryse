"""Synthetic read-only worker/launcher/framing checks. Not live acceptance."""
import ast
import base64
import copy
import hashlib
import lzma
import subprocess
import sys
from unittest.mock import patch
import unittest
import deploy_owner_task_catalog_diagnostic as c
import run_owner_task_catalog_diagnostic as w
from test_owner_codex_catalog_snapshot import fixture
from test_owner_task_isolated_sql import runtime


class DiagnosticTests(unittest.TestCase):
    def test_exact_additive_source_contract(self):
        before={str(i):'a'*64 for i in range(831)};after=dict(before,**{k:'b'*64 for k in c.ADDITIONS})
        c.source_extension(before,after)
        for data in (dict(after,extra='c'*64),dict(after,**{'0':'d'*64})):
            with self.assertRaises(ValueError):c.source_extension(before,data)

    def test_missing_duplicate_drifted_and_oversized_frames_rejected(self):
        record=fixture();wire=w.frames(record)
        self.assertEqual(w.reconstruct(list(reversed(wire)))[0],record)
        variants=[wire[:-1],wire+[wire[0]]]
        changed=copy.deepcopy(wire);changed[0]['record_sha256']='0'*64;variants.append(changed)
        changed=copy.deepcopy(wire);changed[0]['raw_bytes']=w.MAX_RAW+1;variants.append(changed)
        changed=copy.deepcopy(wire);changed[0]['operation_id']='other';variants.append(changed)
        for value in variants:
            with self.assertRaises(Exception):w.reconstruct(value)

    def test_failed_input_never_connects_or_emits_secret(self):
        lines=[];marker='secret-like-synthetic-marker'
        result=w.main(marker,connect=lambda **kw:self.fail('must not connect'),emit=lambda value,**kw:lines.append(value))
        self.assertEqual(result['status'],'unaccepted');self.assertEqual(len(lines),1)
        self.assertNotIn(marker,lines[0]);self.assertFalse(result['step_3_complete'])

    def test_three_closed_fresh_read_only_connections(self):
        opened=[];closed=[];lines=[]
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*args):closed.append(self)
        def connect(**kw):
            self.assertEqual(kw['options'],w.OPTIONS);self.assertIn('default_transaction_read_only=on',kw['options'])
            value=Connection();opened.append(value);return value
        snap=w.snapshot.CatalogSnapshot.from_rows(fixture())
        with patch.object(w.credentials,'validate_admin_secret',return_value={}),patch.object(w.credentials,'require_ca_bundle'),patch.object(w.snapshot,'read_snapshot',return_value=snap):
            result=w.main('synthetic',connect=connect,emit=lambda value,**kw:lines.append(value))
        self.assertEqual(len(opened),3);self.assertEqual(opened,closed)
        self.assertTrue(result['catalog_stable']);self.assertFalse(result['historical_drift_cause_proven'])
        self.assertEqual(result['sql_mutations'],0)

    def plan(self):
        data={p:(c.ROOT/p).read_bytes() for p in c.PATHS}
        return c.build(data,{p:hashlib.sha256(v).hexdigest() for p,v in data.items()},runtime(),'synthetic-mi')

    def decoded(self):
        tree=ast.parse(self.plan()['definition']['properties']['template']['containers'][0]['args'][3])
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='b85decode')
        return lzma.decompress(base64.b85decode(ast.literal_eval(call.args[0].func.value).replace(':',chr(36)))).decode()

    def test_pinned_manual_once_no_migration_or_installer_bundle(self):
        plan=self.plan();config=plan['definition']['properties']['configuration']
        self.assertEqual(config['replicaRetryLimit'],0);self.assertEqual(config['triggerType'],'Manual')
        self.assertEqual(config['identitySettings'],[dict(identity='synthetic-mi',lifecycle='None')])
        script=self.decoded()
        self.assertNotIn('_sources[_migration]',script)
        self.assertNotIn('def install(',script)
        self.assertNotIn('CREATE ROLE',script)
        self.assertIn('sys.modules["run_owner_task_catalog_diagnostic"].main',script)
        self.assertIn('isolated_sql_ambient_override',script)
        self.assertIn('os.environ.pop("OWNERTRIAL_ADMIN_URL",None)',script)

    def test_changed_source_bytes_rejected_before_build(self):
        data={p:(c.ROOT/p).read_bytes() for p in c.PATHS}
        hashes={p:hashlib.sha256(v).hexdigest() for p,v in data.items()}
        data[w.PATH]+=b'\n# unreviewed'
        with self.assertRaises(ValueError):c.build(data,hashes,runtime(),'synthetic-mi')

    def test_controller_preserves_old_native_and_receipt_checks(self):
        old,guard=c.old_context()
        self.assertIsNot(guard,old['guards'])
        self.assertIn('frozen_native_jobs',guard.__code__.co_names)
        self.assertIn('checked_job',guard.__code__.co_names)
        self.assertIn('SQL_FAILURE_SHA',guard.__code__.co_names)
        self.assertEqual(guard.__globals__['catalog_job'],w.JOB)

    def test_framing_rejects_extra_compressed_stream(self):
        wire=w.frames(fixture())
        packed=base64.b64decode(''.join(v['data'] for v in wire))+lzma.compress(b'extra')
        encoded=base64.b64encode(packed).decode();parts=[encoded[i:i+700] for i in range(0,len(encoded),700)]
        binding={k:v for k,v in wire[0].items() if k not in ('index','data')}
        binding.update(compressed_bytes=len(packed),compressed_sha256=hashlib.sha256(packed).hexdigest(),frame_count=len(parts))
        altered=[dict(binding,index=i,data=value) for i,value in enumerate(parts)]
        with self.assertRaises(ValueError):w.reconstruct(altered)

    def test_packed_modules_import_in_separate_process_without_launch(self):
        tree=ast.parse(self.decoded())
        values={node.targets[0].id:ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign)
            and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('_sources','_hashes','_modules')}
        probe='import types,sys,hashlib\n'+''.join(k+'='+repr(v)+'\n' for k,v in values.items())
        probe+='p=types.ModuleType("ibkr_etl");p.__path__=[];sys.modules["ibkr_etl"]=p\n'
        probe+='for path,name in _modules:\n assert hashlib.sha256(_sources[path].encode()).hexdigest()==_hashes[path]\n m=types.ModuleType(name);m.__file__=path;m.__package__="ibkr_etl" if name.startswith("ibkr_etl.") else "";sys.modules[name]=m\n if name.startswith("ibkr_etl."):setattr(p,name.rsplit(".",1)[1],m)\n exec(compile(_sources[path],path,"exec"),m.__dict__)\n'
        result=subprocess.run([sys.executable,'-I','-'],input=probe,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr[-1000:])

    def test_controller_constructs_without_network_or_retargeting_predecessor(self):
        n=c.control();self.assertEqual(n['JOB'],w.JOB_ID)
        self.assertEqual(n['worker'].OPERATION,w.OPERATION)
        self.assertNotEqual(n['JOB'],c.prior.candidate.controller_namespace()['JOB'])
        self.assertIsNot(n['dispatch'],c.generic.dispatch)


if __name__=='__main__':unittest.main()
