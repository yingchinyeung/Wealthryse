"""T report-worker delivery contracts; no registration or cloud writes."""
import json,tempfile,unittest
from pathlib import Path
from ibkr_etl.monthly_artifacts import render_factsheet,factsheet_render_contract_sha256,FACTSHEET_RENDER_CONTRACT_VERSION
from ibkr_etl.alpha_world_t_factsheet import AUTHORITY_SHA256
from validate_subject_presentation import validate_subject_presentation

ROOT=Path(__file__).resolve().parents[1]

class TDeliveryTests(unittest.TestCase):
    def test_factsheet_binds_the_governed_document_pair_without_changing_words(self):
        source=ROOT/'.work/alpha-t-reporting-batch-20261009-v2/inputs/2026-09-30.json'
        if not source.exists():self.skipTest('Private monthly qualification fixture unavailable')
        template=ROOT/'templates/alpha-world-t/owner_template_20261008.pdf'
        with tempfile.TemporaryDirectory() as folder:
            result=render_factsheet(json.loads(source.read_bytes()),template_path=template,
                design_model_path=template,output_path=Path(folder)/'T.pdf')
        self.assertEqual(result['completed_example_sha256'],AUTHORITY_SHA256)
        self.assertEqual(result['supporting_template_sha256'],AUTHORITY_SHA256)
        self.assertEqual(result['render_contract_version'],FACTSHEET_RENDER_CONTRACT_VERSION)
        self.assertEqual(result['render_contract_sha256'],factsheet_render_contract_sha256(
            subject_id='alpha-world-t',supporting_template_sha256=AUTHORITY_SHA256,completed_example_sha256=AUTHORITY_SHA256))

    def test_actual_internal_deck_requires_matching_subject_period_identity_currency(self):
        source=ROOT/'.work/alpha-t-internal-presentations-20261009-v1/inputs/2026-09-30.json'
        output=ROOT/'.work/alpha-t-internal-presentations-20261009-v1/output/AlphaWorld_T_Presentation_2026-09-30.pptx'
        if not output.exists():self.skipTest('Private internal presentation fixture unavailable')
        result=validate_subject_presentation(output,source,subject_id='alpha-world-t',period_end='2026-09-30',expected_strategy='iStrategy: Alpha World T')
        self.assertEqual(result['status'],'passed');self.assertEqual(result['t_financial_audit']['risk_metrics_verified'],13)
        for subject,period,strategy in [('alpha-world-ms','2026-09-30','iStrategy: Alpha World T'),
                                      ('alpha-world-t','2026-08-31','iStrategy: Alpha World T'),
                                      ('alpha-world-t','2026-09-30','iStrategy: Alpha World MS')]:
            with self.subTest(subject=subject,period=period,strategy=strategy),self.assertRaises(ValueError):
                validate_subject_presentation(output,source,subject_id=subject,period_end=period,expected_strategy=strategy)

if __name__=='__main__':unittest.main()
