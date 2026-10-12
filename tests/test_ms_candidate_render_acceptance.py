import calendar
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import seal_ms_candidate_render_acceptance as seal


class CandidateRenderAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = Path(self.temp.name)
        self.image = 'registry/candidate@sha256:' + '1'*64
        self.artifacts, self.visuals, self.pages = [], [], []
        for year, month in [(2025,m) for m in range(1,13)] + [(2026,m) for m in range(1,10)]:
            date = f'{year}-{month:02d}-{calendar.monthrange(year,month)[1]}'
            artifact = {'period_end': date}
            for kind, stem, suffix in (('factsheet','Factsheet','pdf'), ('presentation','Presentation','pptx')):
                path = self.runtime / 'candidate-outputs' / f'AlphaWorld_MS_{stem}_{date}.{suffix}'
                path.parent.mkdir(exist_ok=True)
                path.write_bytes((kind+date).encode())
                artifact[kind+'_sha256'] = seal.build.sha(path)
            self.artifacts.append(artifact)
            matched, previews = [], []
            for slide in range(1,21):
                path = self.runtime / 'cloud-visual-v1' / date / 'slides' / f'slide-{slide:02d}.png'
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes((date+str(slide)).encode())
                matched.append({'slide':slide,'preview_sha256':seal.build.sha(path),
                                'reviewed_reference_sha256':seal.build.sha(path)})
            for page in (1,2):
                path = self.runtime / 'cloud-visual-v1' / date / 'pdf' / f'page-{page}.png'
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes((date+'page'+str(page)).encode())
                entry = {'path':str(path),'sha256':seal.build.sha(path)}
                previews.append(entry)
                self.pages.append(entry)
            self.visuals.append({'period_end':date,'matched_slides':matched,'pdf_previews':previews})
        self.write('candidate-qualified.json', {'status':'passed','image':self.image,
            'proof':{'count':21,'artifacts':self.artifacts},'artifact_readback_verified':True,
            'persistent_jobs_unchanged':True,'protected_receipt':{'protected_readback_verified':True}})
        self.write('cloud-local-audit-v1/receipt.json', {'status':'passed','image':self.image,'months_verified':21,
            'errors':[],'runtime_qualified_sha256':seal.build.sha(self.runtime/'candidate-qualified.json'),
            'items':[{'embedded_workbooks_verified':6} for _ in range(21)]})
        self.write('chart-workbook-acceptance.json', {'status':'passed','image':self.image,'months_checked':21,
            'missing_cloud_chart_workbooks':0,'embedded_workbooks_and_cells_verified':126})
        self.write('cloud-visual-v1/receipt.json', {'status':'reviewed_slide_pixel_identity_passed','image':self.image,
            'runtime_qualified_sha256':seal.build.sha(self.runtime/'candidate-qualified.json'),
            'matched_reviewed_slides':420,'different_slides':0,'factsheets_unchanged_from_verified_baseline':21,
            'pdf_pages_rendered':42,'errors':[],'items':self.visuals})
        self.write('pdf-page-inspection.json', {'status':'passed','pages_inspected':42,'items':self.pages,
            'cloud_visual_receipt_sha256':seal.build.sha(self.runtime/'cloud-visual-v1/receipt.json')})

    def write(self, name, record):
        path = self.runtime / name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(record),encoding='utf-8')

    def mutate(self,name,key,value):
        record = json.loads((self.runtime/name).read_bytes())
        record[key] = value
        self.write(name,record)

    def test_complete_bound_readonly_evidence_passes_without_external_calls(self):
        qualified, evidence = seal.validate_evidence(self.runtime,self.image)
        self.assertEqual(qualified['proof']['count'],21)
        self.assertEqual(evidence['workbook_audit']['embedded_workbooks_and_cells_verified'],126)

    def test_missing_workbook_coverage_blocks(self):
        self.mutate('chart-workbook-acceptance.json','embedded_workbooks_and_cells_verified',125)
        with self.assertRaisesRegex(ValueError,'126 workbooks'):
            seal.validate_evidence(self.runtime,self.image)

    def test_incomplete_visual_coverage_blocks(self):
        self.mutate('cloud-visual-v1/receipt.json','matched_reviewed_slides',419)
        with self.assertRaisesRegex(ValueError,'rendered visual comparison'):
            seal.validate_evidence(self.runtime,self.image)

    def test_incomplete_page_inspection_blocks(self):
        self.mutate('pdf-page-inspection.json','pages_inspected',41)
        with self.assertRaisesRegex(ValueError,'explicitly reviewed PDF pages'):
            seal.validate_evidence(self.runtime,self.image)

    def test_changed_pdf_preview_blocks(self):
        Path(self.pages[0]['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'PDF preview identities changed'):
            seal.validate_evidence(self.runtime,self.image)

    def test_changed_downloaded_report_blocks(self):
        (self.runtime/'candidate-outputs/AlphaWorld_MS_Presentation_2025-01-31.pptx').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'downloaded artifact changed'):
            seal.validate_evidence(self.runtime,self.image)

    def test_changed_slide_preview_blocks(self):
        (self.runtime/'cloud-visual-v1/2025-01-31/slides/slide-06.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'slide preview changed'):
            seal.validate_evidence(self.runtime,self.image)

    def manual_evidence(self):
        record = json.loads((self.runtime/'cloud-visual-v1/receipt.json').read_bytes())
        reviewed = []
        for row in record['items']:
            entry = next(s for s in row['matched_slides'] if s['slide'] == 17)
            row['matched_slides'].remove(entry)
            entry['reviewed_reference_sha256'] = '0'*64
            row['different_slides'] = [entry]
            reviewed.append({'period_end':row['period_end'],'slide':17,
                             'sha256':entry['preview_sha256'],'status':'passed'})
        record.update(status='manual_review_required',matched_reviewed_slides=399,different_slides=21)
        self.write('cloud-visual-v1/receipt.json',record)
        digest = seal.build.sha(self.runtime/'cloud-visual-v1/receipt.json')
        self.mutate('pdf-page-inspection.json','cloud_visual_receipt_sha256',digest)
        self.write('conclusion-layout-comparison.json', {'status':'bounded_autofit_difference_verified',
            'image':self.image,'slides_explained':21,'cloud_visual_receipt_sha256':digest})
        self.write('cloud-slide-inspection.json', {'status':'passed','slides_inspected':21,'items':reviewed,
            'cloud_visual_receipt_sha256':digest,
            'layout_comparison_sha256':seal.build.sha(self.runtime/'conclusion-layout-comparison.json')})

    def test_bound_manual_review_requires_exact_21_conclusion_slides(self):
        self.manual_evidence()
        with patch.object(seal.conclusion,'compare',return_value=[{'scale_only':True}]) as comparison:
            _, evidence = seal.validate_evidence(self.runtime,self.image)
        self.assertEqual(comparison.call_count,21)
        self.assertEqual(evidence['cloud_slide_inspection']['slides_inspected'],21)

    def test_partial_manual_review_blocks(self):
        self.manual_evidence()
        self.mutate('cloud-slide-inspection.json','slides_inspected',20)
        with self.assertRaisesRegex(ValueError,'explicit conclusion-slide inspection'):
            seal.validate_evidence(self.runtime,self.image)

    def test_changed_manual_slide_blocks(self):
        self.manual_evidence()
        (self.runtime/'cloud-visual-v1/2025-01-31/slides/slide-17.png').write_bytes(b'changed')
        with patch.object(seal.conclusion,'compare',return_value=[{'scale_only':True}]):
            with self.assertRaisesRegex(ValueError,'Manually inspected slide preview changed'):
                seal.validate_evidence(self.runtime,self.image)


if __name__ == '__main__':
    unittest.main()
