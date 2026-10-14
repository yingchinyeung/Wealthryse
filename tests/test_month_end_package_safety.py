"""Safety checks the deployed month-end job relies on; synthetic data only."""
import hashlib
import unittest
from unittest.mock import Mock, patch

from azure.core.exceptions import ResourceExistsError

import generate_month_end_package as monthly

SNAPSHOT='751c2b5a-c9cd-54fb-808e-ff98edbd8d67'


def _rows(content=b'pdf'):
    digest=hashlib.sha256(content).hexdigest()
    return [{'report_type':kind,'output_reference':f'reports/review/a/{kind}.bin','output_sha256':digest}
            for kind in ('factsheet','presentation')]


class PackageLockTests(unittest.TestCase):
    def test_busy_lock_refuses_to_run(self):
        connection=Mock();connection.execute.return_value.fetchone.return_value={'acquired':False}
        with self.assertRaisesRegex(RuntimeError,'Another report operation'):
            monthly._acquire_package_lock(connection,'tenant','alpha-world-t','2026-09-30')

    def test_lock_key_is_account_and_month(self):
        connection=Mock();connection.execute.return_value.fetchone.return_value={'acquired':True}
        monthly._acquire_package_lock(connection,'tenant','alpha-world-t','2026-09-30')
        self.assertEqual(connection.execute.call_args.args[1],('report-release:tenant:alpha-world-t:2026-09-30',))


class ExistingArtifactTests(unittest.TestCase):
    def verify(self,rows,content=b'pdf'):
        connection=Mock();connection.execute.return_value.fetchall.return_value=rows
        with patch.object(monthly,'download_blob_bytes',return_value=content):
            monthly._verify_existing_artifacts(connection,tenant_id='tenant',account_id='alpha-world-t',
                snapshot_id=SNAPSHOT,storage_url='https://example.invalid',container='reports')

    def test_matching_pair_passes(self):
        self.verify(_rows())

    def test_changed_file_fails(self):
        with self.assertRaisesRegex(RuntimeError,'integrity'):self.verify(_rows(),content=b'changed')

    def test_incomplete_pair_fails(self):
        with self.assertRaisesRegex(RuntimeError,'incomplete'):self.verify(_rows()[:1])

    def test_reference_outside_container_fails(self):
        rows=_rows();rows[0]['output_reference']='other/review/a/factsheet.bin'
        with self.assertRaisesRegex(RuntimeError,'reference'):self.verify(rows)
        rows=_rows();rows[0]['output_reference']='reports/../secret'
        with self.assertRaisesRegex(RuntimeError,'reference'):self.verify(rows)


class ImmutableUploadTests(unittest.TestCase):
    def test_new_file_is_created_without_overwrite(self):
        with patch.object(monthly,'upload_bytes',return_value=True) as upload, \
             patch.object(monthly,'download_blob_bytes',return_value=b'pdf'):
            self.assertTrue(monthly._upload_immutable_artifact('u','c','n',b'pdf','application/pdf'))
        self.assertIs(upload.call_args.kwargs['overwrite'],False)

    def test_identical_existing_file_is_accepted(self):
        with patch.object(monthly,'upload_bytes',side_effect=ResourceExistsError('exists')), \
             patch.object(monthly,'download_blob_bytes',return_value=b'pdf'):
            self.assertTrue(monthly._upload_immutable_artifact('u','c','n',b'pdf','application/pdf'))

    def test_different_existing_file_is_never_replaced(self):
        with patch.object(monthly,'upload_bytes',side_effect=ResourceExistsError('exists')), \
             patch.object(monthly,'download_blob_bytes',return_value=b'other'):
            with self.assertRaisesRegex(RuntimeError,'differs'):
                monthly._upload_immutable_artifact('u','c','n',b'pdf','application/pdf')


class TemplateVersionTests(unittest.TestCase):
    def test_same_pdf_in_both_roles_keeps_single_authority(self):
        self.assertEqual(monthly._factsheet_template_version('a','a','v2'),'sha256:a+layout:v2')
        self.assertEqual(monthly._factsheet_template_version('a',None,'v2'),'sha256:a+layout:v2')

    def test_separate_design_model_is_recorded(self):
        self.assertEqual(monthly._factsheet_template_version('a','b','v2'),'sha256:b+supporting-sha256:a+layout:v2')


if __name__=='__main__':unittest.main()
