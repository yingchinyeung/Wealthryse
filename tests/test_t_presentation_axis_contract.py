"""T validation must return the verified axis contract consumed by both workers."""
import json,tempfile,unittest,zipfile
from pathlib import Path
from xml.etree import ElementTree as E
from validate_subject_presentation import validate_subject_presentation

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=Path(__file__).parent/'t-axis-fixtures'
if not FIXTURES.exists(): FIXTURES=ROOT/'.work/alpha-t-candidate-reports-20261009-v4/download'
INPUTS=FIXTURES/'inputs'
if not INPUTS.exists(): INPUTS=ROOT/'.work/alpha-t-runtime-release-20261009-v7/worker/context/tests/t-release-fixtures/inputs'

class TAxisContractTests(unittest.TestCase):
    def test_all_monthly_outputs_expose_verified_axis_to_registration(self):
        decks=sorted(FIXTURES.glob('AlphaWorld_T_Presentation_*.pptx'))
        self.assertEqual(len(decks),21)
        for deck in decks:
            period=deck.stem[-10:]
            with self.subTest(period=period):
                result=validate_subject_presentation(deck,INPUTS/(period+'.json'),subject_id='alpha-world-t',period_end=period,expected_strategy='iStrategy: Alpha World T')
                axis=result['performance_axis']
                self.assertEqual(axis['major_unit'],10)
                self.assertEqual(axis['minimum']%10,0)
                self.assertEqual(axis['maximum']%10,0)
                self.assertGreater(axis['maximum'],axis['minimum'])
                self.assertEqual(result['t_financial_audit']['financial_charts_verified'],6)
                self.assertEqual(result['t_financial_audit']['risk_metrics_verified'],13)

    def test_invalid_axis_is_rejected_not_fabricated(self):
        deck=FIXTURES/'AlphaWorld_T_Presentation_2026-09-30.pptx'
        ns='http://schemas.openxmlformats.org/drawingml/2006/chart'
        draw='http://schemas.openxmlformats.org/drawingml/2006/main'
        with tempfile.TemporaryDirectory() as directory:
            changed=Path(directory)/'invalid-axis.pptx'
            count=0
            with zipfile.ZipFile(deck) as source,zipfile.ZipFile(changed,'w') as destination:
                for info in source.infolist():
                    raw=source.read(info.filename)
                    if info.filename.endswith('.xml') and '/charts/chart' in info.filename:
                        root=E.fromstring(raw)
                        title=' '.join(x.text or '' for x in root.iter('{'+draw+'}t'))
                        if 'Rebased performance comparison' in title:
                            root.find('.//{'+ns+'}valAx/{'+ns+'}majorUnit').set('val','5')
                            raw=E.tostring(root); count+=1
                    destination.writestr(info,raw)
            self.assertEqual(count,1)
            with self.assertRaisesRegex(RuntimeError,'10-point'):
                validate_subject_presentation(changed,INPUTS/'2026-09-30.json',subject_id='alpha-world-t',period_end='2026-09-30',expected_strategy='iStrategy: Alpha World T')

if __name__=='__main__': unittest.main()
