import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import qualify_alpha_ms_presentation_months as qualification


class MonthlyPackageReceiptTests(unittest.TestCase):
    def fixture(self,root,*,finding=0,warning=None,digest=None):
        output=root/'deck.pptx';output.write_bytes(b'private output')
        stage=root/'.work/presentation-validation/deck-one';stage.mkdir(parents=True)
        record={'finalSha256':digest or hashlib.sha256(output.read_bytes()).hexdigest(),
            'packageIntegrity':{'finding_count':finding,'exitCode':0,'slide_count':20},
            'presentationLayout':{'finding_count':0,'exitCode':0,
                'warnings':[] if warning is None else [warning]}}
        (stage/'deck.pptx.validation.json').write_text(json.dumps(record),encoding='utf-8')
        return output

    def test_exact_receipt_passes_without_modifying_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=self.fixture(root);before=output.read_bytes()
            with patch.object(qualification,'ROOT',root):
                path,receipt=qualification.matching_finalizer(output)
            self.assertTrue(path.exists());self.assertEqual(receipt['packageIntegrity']['slide_count'],20)
            self.assertEqual(output.read_bytes(),before)

    def test_unmatched_output_or_failed_package_cannot_be_reused(self):
        for mutation in ('digest','finding'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as folder:
                root=Path(folder);output=self.fixture(root,digest='0'*64 if mutation=='digest' else None,
                    finding=1 if mutation=='finding' else 0)
                with patch.object(qualification,'ROOT',root),self.assertRaises(ValueError):
                    qualification.matching_finalizer(output)

    def test_new_overflow_warning_blocks_acceptance(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);output=self.fixture(root,warning={'kind':'table_overflow','slide':5,'object_id':'new'})
            with patch.object(qualification,'ROOT',root),self.assertRaisesRegex(ValueError,'New layout warnings'):
                qualification.matching_finalizer(output)

    def test_approved_cover_background_warning_is_retained_not_called_zero_warnings(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);warning={'kind':'object_outside_slide_boundary','slide':1,'object_id':'125'}
            output=self.fixture(root,warning=warning)
            with patch.object(qualification,'ROOT',root):
                _,receipt=qualification.matching_finalizer(output)
            self.assertEqual(receipt['presentationLayout']['warnings'],[warning])


if __name__=='__main__':unittest.main()
