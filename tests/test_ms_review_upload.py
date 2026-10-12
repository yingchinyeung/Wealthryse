import unittest
from unittest.mock import patch
from azure.core.exceptions import ResourceExistsError
import ms_review_upload as uploader


class MsReviewUploadTests(unittest.TestCase):
    def run_upload(self):
        return uploader.upload_ms_review('https://example.invalid', 'internal-reports',
            'review/alpha-world-ms/month/hash-report.pdf', b'accepted', content_type='application/pdf')

    def test_create_only_and_readback_verified(self):
        with (patch.object(uploader, 'upload_bytes', return_value='url') as upload,
              patch.object(uploader, 'download_blob_bytes', return_value=b'accepted') as read):
            self.assertEqual(self.run_upload(), 'url')
        self.assertFalse(upload.call_args.kwargs['overwrite'])
        read.assert_called_once()

    def test_identical_existing_file_resumes_without_overwrite(self):
        with (patch.object(uploader, 'upload_bytes', side_effect=ResourceExistsError('exists')) as upload,
              patch.object(uploader, 'download_blob_bytes', return_value=b'accepted')):
            self.assertTrue(self.run_upload().startswith('https://example.invalid/'))
        self.assertEqual(upload.call_count, 1)
        self.assertFalse(upload.call_args.kwargs['overwrite'])

    def test_conflicting_existing_file_or_failed_readback_rejected(self):
        for error in (None, ResourceExistsError('exists')):
            with self.subTest(existing=error is not None):
                with (patch.object(uploader, 'upload_bytes', side_effect=error, return_value='url'),
                      patch.object(uploader, 'download_blob_bytes', return_value=b'other')):
                    with self.assertRaisesRegex(RuntimeError, 'differs'):
                        self.run_upload()

    def test_unavailable_storage_and_nonexistence_errors_do_not_bypass_gate(self):
        with patch.object(uploader, 'upload_bytes', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                self.run_upload()
        with (patch.object(uploader, 'upload_bytes', side_effect=PermissionError('denied')),
              patch.object(uploader, 'download_blob_bytes') as read):
            with self.assertRaises(PermissionError):
                self.run_upload()
            read.assert_not_called()
