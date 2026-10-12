"""End-to-end design-update workflow on a real, disposable PostgreSQL schema.

The unit tests mock every query. This suite runs the actual SQL, row-level
security and role grants from migration 062 and bootstrap_postgres.py.
It is skipped unless REPORT_DESIGN_QA_DATABASE_URL names a local database whose
name contains "qa", prepared with scripts/build_disposable_qa_schema.py and then
scripts/bootstrap_postgres.py. The renderer is replaced by a stub that records
generation rows; no image, Azure resource or released report is touched.
"""
from __future__ import annotations

import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse

from ibkr_etl import report_design_updates as service, report_design_worker as worker
from ibkr_etl.db import connect

URL = os.environ.get('REPORT_DESIGN_QA_DATABASE_URL', '')
TENANT = '00000000-0000-0000-0000-000000000001'
OTHER_TENANT = '00000000-0000-0000-0000-0000000000ff'
SUBJECT = 'U16088606'
IMAGE = 'a' * 64
VERSIONS = {'factsheet': 'facts-v9', 'presentation': 'slides-v9'}
PERIODS = ('2026-07-31', '2026-08-31')


def as_role(role):
    parsed = urlparse(URL)
    netloc = role + '@' + (parsed.hostname or '') + (f':{parsed.port}' if parsed.port else '')
    return urlunparse(parsed._replace(netloc=netloc))


def receipt(version):
    gates = {gate: 'passed' for gate in service.GATES}
    gates['runtime_image_sha256'] = IMAGE
    return json.dumps({'subject_id': SUBJECT, 'design_version': version, 'runtime_image_sha256': IMAGE,
                       'template_versions': VERSIONS, 'qualification': gates}, sort_keys=True).encode()


@unittest.skipUnless(URL and 'qa' in urlparse(URL).path, 'Disposable PostgreSQL QA database not configured')
class ReportDesignPostgresTests(unittest.TestCase):
    def connection(self, role='postgres', tenant=TENANT):
        db = connect(Path('/tmp/unused.sqlite3'), as_role(role), tenant)
        self.addCleanup(db.close)
        return db

    def setUp(self):
        admin = self.connection()
        admin.execute('''INSERT INTO tenants(tenant_id,name,status,created_at)
            VALUES (?,'Other QA','active','2026-10-12') ON CONFLICT DO NOTHING''', (OTHER_TENANT,))
        for table in ('active_report_designs', 'report_design_update_requests'):
            admin.execute(f'DELETE FROM {table}')
        admin.execute('''UPDATE report_artifact_releases SET superseded_by_artifact_release_id=NULL''')
        admin.execute('''DELETE FROM report_artifact_releases''')
        admin.execute('''DELETE FROM report_release_decisions''')
        admin.execute('''DELETE FROM report_generation_runs''')
        admin.execute('''DELETE FROM reporting_snapshots WHERE subject_id=?''', (SUBJECT,))
        admin.execute('''DELETE FROM qualified_report_designs''')
        admin.commit()
        self.snapshots = {}
        for period in PERIODS:
            snapshot = str(uuid.uuid4())
            self.snapshots[period] = snapshot
            admin.execute('''INSERT INTO reporting_snapshots(snapshot_id,tenant_id,subject_id,period_start,
                period_end,as_of_at,reporting_currency,formula_version,source_manifest_json,data_sha256,
                quality_status,status,created_at) VALUES (?,?,?,?,?,?,'USD','qa','{}',?,'pass','frozen',?)''',
                (snapshot, TENANT, SUBJECT, period[:8] + '01', period, period, 'd' * 64, period))
            # The month already has an approved, downloadable pair under the old design.
            decision = self.release_pair(admin, snapshot, 'old-design', 'b')
            self.assertIsNotNone(decision)
        admin.commit()

    def release_pair(self, db, snapshot, template_suffix, hex_char):
        decision = db.execute('''INSERT INTO report_release_decisions(tenant_id,snapshot_id,release_scope,
            status,approved_by,approved_at,conditions_json) VALUES (?,?,'internal_shareholder','approved',
            'owner@example.test','2026-10-12','{}') RETURNING release_decision_id''',
            (TENANT, snapshot)).fetchone()[0]
        for kind in ('factsheet', 'presentation'):
            run = db.execute('''INSERT INTO report_generation_runs(tenant_id,snapshot_id,report_type,
                template_version,status,output_reference,output_sha256,started_at)
                VALUES (?,?,?,?,'released_internal','private/qa',?,'2026-10-12') RETURNING generation_run_id''',
                (TENANT, snapshot, kind, VERSIONS[kind] + '+' + template_suffix, hex_char * 64)).fetchone()[0]
            db.execute('''INSERT INTO report_artifact_releases(tenant_id,release_decision_id,generation_run_id,
                release_scope,container_name,blob_name,artifact_sha256,released_at)
                VALUES (?,?,?,'internal_shareholder','internal-reports',?,?,'2026-10-12')''',
                (TENANT, decision, run, f'qa/{run}', hex_char * 64))
        return decision

    def register(self, version='design-qa-1'):
        admin = self.connection()
        row = service.register_design(admin, TENANT, SUBJECT, version, receipt(version),
                                      installed_image_sha256=IMAGE, actor='release-service')
        admin.commit()
        return row

    def test_website_approval_role_cannot_register_or_activate_designs(self):
        import psycopg
        self.register()
        for statement in ('''INSERT INTO qualified_report_designs(subject_id,design_version,runtime_image_sha256,
                                template_versions_json,qualification_json,qualified_at)
                             VALUES ('U16088606','forged','''+"'"+'a'*64+"'"+''','{}','{}','2026')''',
                          "UPDATE qualified_report_designs SET retired_at=NULL",
                          "DELETE FROM report_design_update_requests",
                          '''INSERT INTO active_report_designs(subject_id,design_version,request_id,activated_at)
                             VALUES ('U16088606','design-qa-1',gen_random_uuid(),'2026')'''):
            with self.subTest(statement=statement[:40]):
                approval = self.connection('ibkr_approval')
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    approval.execute(statement)
                approval.rollback()

    def test_rows_are_invisible_and_unwritable_across_tenants(self):
        import psycopg
        self.register()
        approval = self.connection('ibkr_approval')
        service.create_request(approval, TENANT, SUBJECT, uuid.uuid4(), 'Move the benchmark caption', 'owner')
        approval.commit()
        other = self.connection('ibkr_approval', OTHER_TENANT)
        self.assertEqual(other.execute('SELECT count(*) FROM qualified_report_designs').fetchone()[0], 0)
        self.assertEqual(other.execute('SELECT count(*) FROM report_design_update_requests').fetchone()[0], 0)
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            other.execute('''INSERT INTO report_design_update_requests(request_id,tenant_id,subject_id,
                requested_by,requested_at,description,status,updated_at)
                VALUES (gen_random_uuid(),?,?,'x','2026','cross-tenant write','requested','2026')''',
                (TENANT, SUBJECT))

    def test_registration_is_create_only(self):
        self.register()
        admin = self.connection()
        rebound = json.loads(receipt('design-qa-1'))
        rebound['template_versions'] = {'factsheet': 'other', 'presentation': 'other'}
        with self.assertRaises(ValueError):
            service.register_design(admin, TENANT, SUBJECT, 'design-qa-1', json.dumps(rebound).encode(),
                                    installed_image_sha256=IMAGE, actor='release-service')

    def test_full_workflow_replaces_downloads_and_activates_only_after_every_approval(self):
        self.register()
        approval = self.connection('ibkr_approval')
        request = str(uuid.uuid4())
        service.create_request(approval, TENANT, SUBJECT, request, 'Use the corrected sector layout', 'owner')
        plan = service.load_plan_inputs(approval, TENANT, SUBJECT, 'design-qa-1', installed_image_sha256=IMAGE)
        self.assertEqual([item['period_end'] for item in plan['snapshots']], list(PERIODS))
        service.queue_request(approval, TENANT, SUBJECT, request, 'design-qa-1', service.digest(plan), 'owner',
                              installed_image_sha256=IMAGE)
        approval.commit()

        # A second live request for the same account is refused by the database itself.
        import psycopg
        other = str(uuid.uuid4())
        service.create_request(approval, TENANT, SUBJECT, other, 'A competing design request', 'owner')
        approval.commit()
        with self.assertRaises(psycopg.errors.UniqueViolation):
            approval.execute('''UPDATE report_design_update_requests SET status='queued',design_version=?,
                plan_json=?,plan_sha256=? WHERE request_id=?''',
                ('design-qa-1', '{}', 'e' * 64, other))
        approval.rollback()

        app = self.connection('ibkr_app')

        def renderer(command, *, env, **kwargs):
            self.assertTrue(command[-1].endswith('scripts/generate_month_end_package.py'))
            snapshot = env['REPORT_BOUND_SNAPSHOT_ID']
            self.assertEqual(snapshot, self.snapshots[env['REPORT_PERIOD_END']])
            for kind in ('factsheet', 'presentation'):
                app.execute('''INSERT INTO report_generation_runs(tenant_id,snapshot_id,report_type,
                    template_version,status,output_reference,output_sha256,started_at)
                    VALUES (?,?,?,?,'awaiting_approval','private/qa-new',?,'2026-10-12')''',
                    (TENANT, snapshot, kind, VERSIONS[kind] + '+design:design-qa-1', 'c' * 64))
            app.commit()

        environment = {'REPORT_DESIGN_UPDATES_ENABLED': 'true', 'REPORT_RENDERER_IMAGE_SHA256': IMAGE}
        with patch.dict(os.environ, environment):
            result = worker.process_next_update(app, TENANT, Path('/qa'), run=renderer)
        self.assertEqual(result['status'], 'awaiting_review')
        self.assertFalse(result['reports_released'])
        old_live = '''SELECT count(*) FROM report_artifact_releases WHERE revoked_at IS NULL
                      AND superseded_by_artifact_release_id IS NULL'''
        self.assertEqual(app.execute(old_live).fetchone()[0], 4, 'old downloads stay live during review')

        # Approve the first month only: the design must not activate yet.
        def approve(period):
            snapshot = self.snapshots[period]
            # Like the website, a month keeps one decision; replacements attach to it.
            decision = approval.execute('''SELECT release_decision_id FROM report_release_decisions
                WHERE snapshot_id=? AND release_scope='internal_shareholder' AND status='approved' ''',
                (snapshot,)).fetchone()[0]
            for run in approval.execute('''SELECT generation_run_id,report_type FROM report_generation_runs
                    WHERE snapshot_id=? AND status='awaiting_approval' ''', (snapshot,)).fetchall():
                approval.execute("UPDATE report_generation_runs SET status='released_internal' WHERE generation_run_id=?",
                                 (run['generation_run_id'],))
                new = approval.execute('''INSERT INTO report_artifact_releases(tenant_id,release_decision_id,
                    generation_run_id,release_scope,container_name,blob_name,artifact_sha256,released_at)
                    VALUES (?,?,?,'internal_shareholder','internal-reports','qa/new',?,'2026-10-12')
                    RETURNING artifact_release_id''', (TENANT, decision, run['generation_run_id'], 'c' * 64)).fetchone()[0]
                withdrawn = service.supersede_prior_releases(approval, TENANT, SUBJECT, period, run['report_type'],
                                                             new, 'owner', '2026-10-12')
                self.assertEqual(len(withdrawn), 1)
            approval.commit()

        def finish():
            with patch.dict(os.environ, environment):
                worker.finish_reviewed_updates(app, TENANT)
            app.commit()
            return app.execute('SELECT design_version FROM active_report_designs WHERE subject_id=?',
                               (SUBJECT,)).fetchone()

        approve(PERIODS[0])
        self.assertIsNone(finish())
        approve(PERIODS[1])
        self.assertEqual(finish()['design_version'], 'design-qa-1')
        status = app.execute('SELECT status FROM report_design_update_requests WHERE request_id=?',
                             (request,)).fetchone()['status']
        self.assertEqual(status, 'completed')
        rows = app.execute('''SELECT count(*) FILTER (WHERE superseded_by_artifact_release_id IS NOT NULL) AS old,
            count(*) FILTER (WHERE revoked_at IS NULL) AS live FROM report_artifact_releases''').fetchone()
        self.assertEqual((rows['old'], rows['live']), (4, 4), 'old files archived, never deleted')

    def test_changed_frozen_input_after_consent_fails_without_release(self):
        self.register()
        approval = self.connection('ibkr_approval')
        request = str(uuid.uuid4())
        service.create_request(approval, TENANT, SUBJECT, request, 'Tighten the footer spacing', 'owner')
        plan = service.load_plan_inputs(approval, TENANT, SUBJECT, 'design-qa-1', installed_image_sha256=IMAGE)
        service.queue_request(approval, TENANT, SUBJECT, request, 'design-qa-1', service.digest(plan), 'owner',
                              installed_image_sha256=IMAGE)
        approval.commit()
        admin = self.connection()
        # Frozen rows are immutable (a trigger refuses edits), so a correction is a newer snapshot.
        admin.execute('''INSERT INTO reporting_snapshots(snapshot_id,tenant_id,subject_id,period_start,
            period_end,as_of_at,reporting_currency,formula_version,source_manifest_json,data_sha256,
            quality_status,status,created_at) VALUES (?,?,?,'2026-07-01','2026-07-31','2026-07-31','USD','qa',
            '{}',?,'pass','frozen','2026-10-13')''', (str(uuid.uuid4()), TENANT, SUBJECT, 'e' * 64))
        admin.commit()
        app = self.connection('ibkr_app')
        environment = {'REPORT_DESIGN_UPDATES_ENABLED': 'true', 'REPORT_RENDERER_IMAGE_SHA256': IMAGE}
        with patch.dict(os.environ, environment), self.assertRaises(ValueError):
            worker.process_next_update(app, TENANT, Path('/qa'), run=lambda *a, **k: self.fail('rendered'))
        self.assertEqual(app.execute('SELECT status FROM report_design_update_requests WHERE request_id=?',
                                     (request,)).fetchone()['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
