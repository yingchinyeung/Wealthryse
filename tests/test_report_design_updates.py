from __future__ import annotations

import copy
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from ibkr_etl import report_design_updates as service, report_design_worker as worker, web
from ibkr_etl.report_design_ui import SCRIPT, controls
from generate_month_end_package import _existing_package, _pending_design_update

IMAGE='a'*64
TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-ms'
ID='751c2b5a-c9cd-54fb-808e-ff98edbd8d67'
VERSIONS={'factsheet':'facts-v2','presentation':'slides-v2'}


def design():
    evidence={gate:'passed' for gate in service.GATES}
    evidence.update(runtime_image_sha256=IMAGE,evidence_sha256='c'*64)
    return {'subject_id':SUBJECT,'design_version':'design-v2','runtime_image_sha256':IMAGE,
        'template_versions_json':json.dumps(VERSIONS),'qualification_json':json.dumps(evidence)}


def snapshots():
    return [{'subject_id':SUBJECT,'snapshot_id':ID,'period_end':'2026-09-30',
        'data_sha256':'b'*64,'status':'frozen','quality_status':'pass'}]


def plan():
    return service.build_plan(SUBJECT,design(),snapshots(),['2026-09-30'],installed_image_sha256=IMAGE)


class DesignServiceTests(unittest.TestCase):
    def test_plan_is_deterministic_binds_sources_and_archives_prior_reports(self):
        original=copy.deepcopy(snapshots()); result=plan()
        self.assertEqual(service.digest(result),service.digest(copy.deepcopy(result)))
        self.assertEqual(result['snapshots'][0]['data_sha256'],'b'*64)
        self.assertEqual(result['source_policy'],'unchanged_frozen_inputs')
        self.assertEqual(result['replacement_policy'],'replace_on_owner_approval')
        self.assertTrue(result['future_months'])
        self.assertEqual(snapshots(),original)

    def test_every_qualification_gate_is_required(self):
        for gate in service.GATES:
            with self.subTest(gate=gate):
                row=design(); evidence=json.loads(row['qualification_json']); evidence[gate]='pending'
                row['qualification_json']=json.dumps(evidence)
                with self.assertRaisesRegex(ValueError,'incomplete'):
                    service.qualified_design(row,installed_image_sha256=IMAGE)

    def test_wrong_image_receipt_or_retired_design_fails_closed(self):
        for mutation in ('image','retired','receipt','evidence_image','template'):
            row=design();evidence=json.loads(row['qualification_json'])
            if mutation=='image': row['runtime_image_sha256']='d'*64
            elif mutation=='retired':row['retired_at']='2026-10-07'
            elif mutation=='receipt':evidence['evidence_sha256']='invalid'
            elif mutation=='evidence_image':evidence['runtime_image_sha256']='d'*64
            else:row['template_versions_json']='{"factsheet":"v2"}'
            row['qualification_json']=json.dumps(evidence)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                service.qualified_design(row,installed_image_sha256=IMAGE)

    def test_frozen_snapshot_scope_quality_digest_and_period_are_required(self):
        for key,value in [('subject_id','other'),('quality_status','fail'),('status','draft'),
                          ('data_sha256','bad'),('period_end','2026-09-29'),('snapshot_id','invalid')]:
            rows=snapshots();rows[0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                service.build_plan(SUBJECT,design(),rows,[],installed_image_sha256=IMAGE)

    def test_missing_previously_released_month_empty_or_duplicate_inputs_rejected(self):
        for rows,months in [(snapshots(),['2026-08-31']),([],[]),(snapshots()*2,[])]:
            with self.subTest(months=months),self.assertRaises(ValueError):
                service.build_plan(SUBJECT,design(),rows,months,installed_image_sha256=IMAGE)

    def test_request_retry_is_idempotent_and_not_rebindable(self):
        connection=Mock(); old={'subject_id':SUBJECT,'requested_by':'owner','description':'Improve chart legends'}
        connection.execute.return_value.fetchone.return_value=old
        self.assertEqual(service.create_request(connection,TENANT,SUBJECT,ID,old['description'],'owner'),old)
        with self.assertRaisesRegex(ValueError,'different input'):
            service.create_request(connection,TENANT,SUBJECT,ID,'Different chart title','owner')
        self.assertFalse(any('INSERT INTO' in call.args[0] for call in connection.execute.call_args_list))

    def test_stale_plan_does_not_queue(self):
        connection=Mock();connection.execute.return_value.fetchone.side_effect=[
            {'status':'requested'},None]
        with patch.object(service,'load_plan_inputs',return_value=plan()):
            with self.assertRaisesRegex(ValueError,'preview again'):
                service.queue_request(connection,TENANT,SUBJECT,ID,'design-v2','d'*64,'owner',installed_image_sha256=IMAGE)
        self.assertFalse(any('UPDATE ' in call.args[0] for call in connection.execute.call_args_list))

    def test_qualification_registration_is_create_only_and_receipt_bound(self):
        row=design();receipt={'subject_id':SUBJECT,'design_version':'design-v2','runtime_image_sha256':IMAGE,
            'template_versions':VERSIONS,'qualification':json.loads(row['qualification_json'])}
        content=json.dumps(receipt).encode(); connection=Mock()
        connection.execute.return_value.fetchone.side_effect=[None,row]
        self.assertEqual(service.register_design(connection,TENANT,SUBJECT,'design-v2',content,
            installed_image_sha256=IMAGE,actor='operator'),row)
        sql='\n'.join(c.args[0] for c in connection.execute.call_args_list)
        self.assertIn('INSERT INTO qualified_report_designs',sql)
        self.assertNotIn('UPDATE qualified',sql)
        with self.assertRaisesRegex(ValueError,'another account'):
            service.register_design(connection,TENANT,'other','design-v2',content,installed_image_sha256=IMAGE,actor='operator')

    def test_supersession_is_scoped_and_never_deletes(self):
        connection=Mock();connection.execute.return_value.fetchall.return_value=[{'artifact_release_id':2}]
        result=service.supersede_prior_releases(connection,TENANT,SUBJECT,'2026-09-30','factsheet',3,'owner','now')
        self.assertEqual(result,[{'artifact_release_id':2}]);sql,parameters=connection.execute.call_args.args
        for required in ('a.tenant_id=?','s.subject_id=?','s.period_end=?','r.report_type=?',
                         "a.release_scope='internal_shareholder'",'superseded_by_artifact_release_id=?'):
            self.assertIn(required,sql)
        self.assertNotIn('DELETE',sql);self.assertIn(SUBJECT,parameters)


class DesignApiTests(unittest.TestCase):
    owner={'role':'owner','email':'owner@example.com','object_id':'owner'}

    def test_nonowner_and_onboarding_are_denied_before_database(self):
        with patch.object(web,'_database',side_effect=AssertionError('no database')):
            with self.assertRaises(HTTPException) as denied:
                web.list_report_design_updates(SUBJECT,{'role':'viewer'})
            self.assertEqual(denied.exception.status_code,403)
            with patch.object(web,'_portfolio_access_context',return_value={'preview_mode':True}):
                with self.assertRaises(HTTPException) as denied:
                    web.list_report_design_updates(SUBJECT,self.owner)
                self.assertEqual(denied.exception.status_code,409)

    def test_disabled_worker_blocks_preview_and_queuing(self):
        payload=web.ReportDesignQueueRequest(design_version='design-v2',plan_sha256='c'*64,
            replace_prior_releases=True,apply_to_future=True)
        with patch.dict(os.environ,{'REPORT_DESIGN_UPDATES_ENABLED':'false'}), \
             patch.object(web,'_portfolio_access_context',return_value={'preview_mode':False}), \
             patch.object(web,'_database',side_effect=AssertionError('no database')):
            for invoke in [lambda:web.preview_report_design_update(SUBJECT,'design-v2',self.owner),
                           lambda:web.queue_report_design_update(SUBJECT,uuid.UUID(ID),payload,self.owner)]:
                with self.assertRaises(HTTPException) as denied:invoke()
                self.assertEqual(denied.exception.status_code,503)

    def test_consent_and_unknown_fields_are_not_optional(self):
        data={'design_version':'design-v2','plan_sha256':'c'*64,
            'replace_prior_releases':True,'apply_to_future':True}
        for change in ({'replace_prior_releases':False},{'apply_to_future':False},
                       {'plan_sha256':'bad'},{'code':'execute this'}):
            with self.subTest(change=change),self.assertRaises(ValidationError):
                web.ReportDesignQueueRequest(**(data|change))

    def test_controls_only_render_for_active_owner_and_escape_account(self):
        access={'display_name':'Alpha World MS','reporting_currency':'HKD',
            'history_start_date':'2025-01-20','preview_mode':False}
        owner=web._render_account_page(self.owner,account_id=SUBJECT,section='reports',access=access)
        self.assertIn('id="report-design-controls"',owner)
        for role,preview in [('viewer',False),('owner',True)]:
            html=web._render_account_page(self.owner|{'role':role},account_id=SUBJECT,
                section='reports',access=access|{'preview_mode':preview})
            self.assertNotIn('id="report-design-controls"',html)
        self.assertNotIn('<script>alert',controls('<script>alert(1)</script>'))
        self.assertIn('textContent',SCRIPT)
        self.assertNotIn('innerHTML',SCRIPT)


class DesignWorkerTests(unittest.TestCase):
    def test_disabled_worker_does_nothing(self):
        connection=Mock();run=Mock()
        with patch.dict(os.environ,{'REPORT_DESIGN_UPDATES_ENABLED':'false'}):
            self.assertEqual(worker.process_next_update(connection,TENANT,Path('.'),run=run),{'status':'disabled'})
        connection.execute.assert_not_called();run.assert_not_called()

    def pair_rows(self):
        return [{'generation_run_id':idx,'report_type':kind,'template_version':value+'+design:design-v2',
            'status':'awaiting_approval','output_sha256':'e'*64,'output_reference':'private/file'}
            for idx,(kind,value) in enumerate(VERSIONS.items(),1)]

    def test_worker_requires_complete_exact_version_and_valid_hash(self):
        for mutation in ('missing','version','hash','status','reference'):
            rows=self.pair_rows()
            if mutation=='missing':rows=rows[:1]
            elif mutation=='version':rows[0]['template_version']='old'
            elif mutation=='hash':rows[0]['output_sha256']='invalid'
            elif mutation=='status':rows[0]['status']='running'
            else:rows[0]['output_reference']=''
            connection=Mock();connection.execute.return_value.fetchall.return_value=rows
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                worker.verify_generated_pair(connection,TENANT,plan(),snapshots()[0])

    def worker_connection(self):
        connection=Mock();connection.execute.return_value.rowcount=1
        current=plan(); row={'subject_id':SUBJECT,'request_id':ID,'plan_json':current,
            'plan_sha256':service.digest(current),'design_version':'design-v2','result_json':'{}'}
        connection.execute.return_value.fetchone.side_effect=[row,{'lifecycle_status':'active'}]
        return connection,current

    def test_worker_uses_only_fixed_command_and_exact_bound_inputs_never_releases(self):
        connection,current=self.worker_connection();run=Mock();pair={'factsheet':{'generation_run_id':1,'sha256':'e'*64},
            'presentation':{'generation_run_id':2,'sha256':'f'*64}}
        with patch.dict(os.environ,{'REPORT_DESIGN_UPDATES_ENABLED':'true','REPORT_RENDERER_IMAGE_SHA256':IMAGE,
                                    'FACTSHEET_TEMPLATE_BLOB':'other/account.pdf'}), \
             patch.object(worker,'finish_reviewed_updates'),patch.object(worker,'load_plan_inputs',return_value=current), \
             patch.object(worker,'verify_generated_pair',return_value=pair):
            result=worker.process_next_update(connection,TENANT,Path('project'),run=run)
        self.assertEqual(result['status'],'awaiting_review');self.assertFalse(result['reports_released'])
        args,kwargs=run.call_args;self.assertEqual(Path(args[0][1]).name,'generate_month_end_package.py')
        self.assertEqual(kwargs['env']['REPORT_BOUND_SNAPSHOT_ID'],ID)
        self.assertEqual(kwargs['env']['REPORT_BOUND_SNAPSHOT_SHA256'],'b'*64)
        self.assertNotIn('FACTSHEET_TEMPLATE_BLOB',kwargs['env'])
        sql='\n'.join(c.args[0] for c in connection.execute.call_args_list)
        self.assertNotIn('INSERT INTO report_artifact_releases',sql)
        self.assertNotIn('UPDATE reporting_snapshots',sql)
        self.assertIn('lease_token=?',sql)

    def test_changed_input_or_renderer_failure_does_not_release(self):
        for changed in (True,False):
            connection,current=self.worker_connection();run=Mock(side_effect=RuntimeError('renderer failed'))
            with patch.dict(os.environ,{'REPORT_DESIGN_UPDATES_ENABLED':'true'}), \
                 patch.object(worker,'finish_reviewed_updates'), \
                 patch.object(worker,'load_plan_inputs',return_value={} if changed else current):
                with self.assertRaises((ValueError,RuntimeError)):
                    worker.process_next_update(connection,TENANT,Path('.'),run=run)
            if changed:run.assert_not_called()
            self.assertTrue(connection.rollback.called)
            self.assertTrue(any("status='failed'" in c.args[0] for c in connection.execute.call_args_list))

    def test_future_design_not_activated_until_every_replacement_is_approved(self):
        current=plan();pair={'factsheet':{'generation_run_id':1,'sha256':'e'*64},
                            'presentation':{'generation_run_id':2,'sha256':'f'*64}}
        row={'plan_json':current,'plan_sha256':service.digest(current),'result_json':{'2026-09-30':pair},
            'subject_id':SUBJECT,'request_id':ID,'design_version':'design-v2'}
        for complete in (False,True):
            connection=Mock()
            connection.execute.return_value.fetchall.side_effect=[[row],
                [{'generation_run_id':1,'artifact_sha256':'e'*64},{'generation_run_id':2,'artifact_sha256':'f'*64}]
                if complete else [{'generation_run_id':1,'artifact_sha256':'e'*64}]]
            connection.execute.return_value.fetchone.side_effect=[snapshots()[0],{'lifecycle_status':'active'},design()]
            with patch.dict(os.environ,{'REPORT_RENDERER_IMAGE_SHA256':IMAGE}), \
                 patch.object(worker,'verify_generated_pair',return_value=pair):
                worker.finish_reviewed_updates(connection,TENANT)
            activated=any('INSERT INTO active_report_designs' in c.args[0] for c in connection.execute.call_args_list)
            self.assertEqual(activated,complete)


class DesignGeneratorTests(unittest.TestCase):
    def test_existing_pair_check_binds_exact_snapshot_not_just_month(self):
        connection=Mock();connection.execute.return_value.fetchall.return_value=[]
        self.assertIsNone(_existing_package(connection,tenant_id=TENANT,account_id=SUBJECT,
            period_end='2026-09-30',expected_versions=VERSIONS,allow_replacement=True,snapshot_id=ID))
        sql,parameters=connection.execute.call_args.args
        self.assertIn('s.snapshot_id = CAST(? AS uuid)',sql)
        self.assertEqual(parameters[-2:],(ID,ID))

    def test_normal_scheduler_cannot_overwrite_month_under_design_review(self):
        connection=Mock();current=plan()
        connection.execute.return_value.fetchone.return_value={'plan_json':current,'plan_sha256':service.digest(current)}
        self.assertTrue(_pending_design_update(connection,tenant_id=TENANT,account_id=SUBJECT,period_end='2026-09-30'))
        self.assertFalse(_pending_design_update(connection,tenant_id=TENANT,account_id=SUBJECT,period_end='2026-10-31'))

    def test_qualified_design_can_replace_old_released_pair_but_normal_job_cannot(self):
        connection=Mock(); rows=[{'report_type':kind,'status':'released_internal','template_version':'old'}
            for kind in VERSIONS]
        connection.execute.return_value.fetchall.return_value=rows
        kwargs=dict(tenant_id=TENANT,account_id=SUBJECT,period_end='2026-09-30',expected_versions=VERSIONS)
        self.assertEqual(_existing_package(connection,**kwargs),'already_released')
        self.assertIsNone(_existing_package(connection,**kwargs,allow_replacement=True))
        for row in rows:row['template_version']=VERSIONS[row['report_type']]
        self.assertEqual(_existing_package(connection,**kwargs,allow_replacement=True),'already_released')

    def test_qualified_retry_accepts_partial_release_but_normal_job_fails(self):
        connection=Mock();connection.execute.return_value.fetchall.return_value=[
            {'report_type':'factsheet','status':'released_internal','template_version':'old'}]
        kwargs=dict(tenant_id=TENANT,account_id=SUBJECT,period_end='2026-09-30',expected_versions=VERSIONS)
        with self.assertRaisesRegex(RuntimeError,'partial release'):_existing_package(connection,**kwargs)
        self.assertIsNone(_existing_package(connection,**kwargs,allow_replacement=True))


if __name__=='__main__':unittest.main()
