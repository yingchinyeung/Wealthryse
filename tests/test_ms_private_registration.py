import ast
import copy
import importlib
from pathlib import Path
import unittest

import register_ms_private_batch_runtime_v2 as runtime
from uuid import UUID
from ibkr_etl.util import canonical_json


class RegistrationTests(unittest.TestCase):
    def test_uuid_metadata_serialization_is_explicit_and_recursive(self):
        identifier=UUID('00000000-0000-0000-0000-000000000001')
        value={'case_id':identifier,'nested':[{'snapshot_id':identifier}]}
        with self.assertRaises(TypeError):canonical_json(value)
        normalized=runtime.normalize_uuids(value)
        self.assertEqual(normalized,{'case_id':str(identifier),'nested':[{'snapshot_id':str(identifier)}]})
        self.assertIn(str(identifier),canonical_json(normalized))
        self.assertIs(value['case_id'],identifier)

    def test_normalization_does_not_hide_unsupported_types(self):
        with self.assertRaises(TypeError):canonical_json(runtime.normalize_uuids({'unexpected':object()}))

    def fixture(self):
        before={k:[] for k in ('subjects','cases','approvals','templates','snapshots','releases','items','runs','batches')}
        before['subjects']=[{'subject_id':'alpha-world-ms','lifecycle_status':'onboarding'}]
        before['batches']=[{'batch_id':'old','status':'awaiting_owner_approval','manifest_json':{'baseline_content_sha256':'old'}}]
        before['items']=[{'batch_id':'old','artifact_sha256':'old'}]
        before['runs']=[{'generation_run_id':1,'output_sha256':'old'}]
        after=copy.deepcopy(before);after['batches'][0]['status']='superseded'
        after['batches'].append({'batch_id':'new','status':'awaiting_owner_approval'})
        after['items'].append({'batch_id':'new','artifact_sha256':'new'})
        after['runs'].append({'generation_run_id':2,'output_sha256':'new'})
        return before,after

    def test_only_old_private_batch_status_changes(self):
        runtime.unchanged(*self.fixture(),'old','new')

    def test_every_protected_category_fails_closed(self):
        for category in ('subjects','cases','approvals','templates','snapshots','releases','items','runs','batches'):
            with self.subTest(category=category):
                before,after=self.fixture()
                after[category].append({'unauthorized':'change','batch_id':'other','generation_run_id':1})
                with self.assertRaises(ValueError):runtime.unchanged(before,after,'old','new')

    def test_previous_generation_hash_cannot_change(self):
        before,after=self.fixture();after['runs'][0]['output_sha256']='tampered'
        with self.assertRaises(ValueError):runtime.unchanged(before,after,'old','new')

    def test_previous_item_cannot_disappear(self):
        before,after=self.fixture();after['items'].pop(0)
        with self.assertRaises(ValueError):runtime.unchanged(before,after,'old','new')

    def test_sql_writes_are_metadata_only_and_never_delete(self):
        tree=ast.parse(Path(runtime.__file__).read_text())
        targets=set()
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute) or node.func.attr!='execute' or not node.args:continue
            value=node.args[0]
            if not isinstance(value,ast.Constant) or not isinstance(value.value,str):continue
            sql=value.value.strip().upper()
            self.assertFalse(sql.startswith(('DELETE','DROP','ALTER','TRUNCATE')))
            if sql.startswith('INSERT INTO'):targets.add(sql.split()[2].split('(')[0])
            elif sql.startswith('UPDATE'):targets.add(sql.split()[1])
        self.assertEqual(targets,{'ONBOARDING_HISTORICAL_REPORT_BATCHES','ONBOARDING_HISTORICAL_REPORT_ITEMS','REPORT_GENERATION_RUNS','AUDIT_EVENTS'})

    def test_source_approval_and_onboarding_tables_never_written(self):
        source=Path(runtime.__file__).read_text()
        for word in ('UPDATE platform_subjects','UPDATE account_onboarding','UPDATE reporting_snapshots','UPDATE report_generation_runs','INSERT INTO report_artifact_releases','nav_snapshots','position_snapshots'):
            self.assertNotIn(word,source)


if __name__=='__main__':unittest.main()
