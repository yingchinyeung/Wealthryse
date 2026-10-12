"""Qualification regressions use frozen private inputs; no database writes."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as E

from prepare_alpha_t_private_presentation import ROOT, HISTORY, prepare, build_model
from audit_alpha_t_private_presentation import audit, A


class PrivateTQualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=ROOT/'.work/alpha-t-presentations-qualification-v4'
        cls.september=json.loads((cls.base/'inputs/2026-09-30.json').read_bytes())

    def test_original_metrics_preserved(self):
        m=build_model(self.september)
        original=self.september['extended_risk']['risk']['original_metrics']
        self.assertTrue(all(m['risk'][k]==v for k,v in original.items()))
        self.assertIsNone(m['risk']['crisis_maximum_drawdown'])
        self.assertFalse(m['crisis_available'])

    def test_reject_changed_risk(self):
        for field,value in [('expected_maximum_drawdown_1y',0),('drawdown_simulation_paths',100)]:
            with self.subTest(field=field):
                s=copy.deepcopy(self.september);s['extended_risk']['risk'][field]=value
                with self.assertRaises(ValueError):build_model(s)

    def test_reject_changed_proof_hash(self):
        s=copy.deepcopy(self.september);s['qualification_evidence']['extended_risk_sha256']='0'*64
        with self.assertRaises(ValueError):build_model(s)

    def test_reject_live_flag(self):
        s=copy.deepcopy(self.september);s['publication_allowed']=True
        with self.assertRaises(AssertionError):build_model(s)

    def test_january_short_history(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'jan.json';prepare('2025-01-31',p,extended_risk=True)
            risk=build_model(json.loads(p.read_bytes()))['risk']
        self.assertEqual(risk['drawdown_simulation_paths'],0)
        for key in ('expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y'):
            self.assertIsNone(risk[key])

    def test_february_bootstrap_without_full_observed_year(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'feb.json';prepare('2025-02-28',p,extended_risk=True)
            risk=build_model(json.loads(p.read_bytes()))['risk']
        self.assertEqual(risk['drawdown_simulation_paths'],5000)
        self.assertIsNone(risk['actual_maximum_drawdown_1y'])
        self.assertLess(risk['tail_maximum_drawdown_1y'],risk['expected_maximum_drawdown_1y'])

    def test_exported_cards_cannot_swap_equal_looking_locations(self):
        # The original v4 deck predates the native-PowerPoint overlap repair.
        # Keep it as a negative fixture, not as an accepted current baseline.
        with self.assertRaisesRegex(AssertionError, 'Allocation note overlaps'):
            audit(self.base/'output/AlphaWorld_T_Presentation_2026-09-30_Private.pptx',
                  self.base/'inputs/2026-09-30.json')
        fixtures=Path(__file__).parent/'t-axis-fixtures'
        if not fixtures.exists():fixtures=ROOT/'.work/alpha-t-candidate-reports-20261009-v4/download'
        inputs=fixtures/'inputs'
        if not inputs.exists():inputs=ROOT/'.work/alpha-t-runtime-release-20261009-v7/worker/context/tests/t-release-fixtures/inputs'
        deck=fixtures/'AlphaWorld_T_Presentation_2026-09-30.pptx'
        source=inputs/'2026-09-30.json'
        audit(deck,source)
        with tempfile.TemporaryDirectory() as folder:
            changed=Path(folder)/'changed.pptx'
            with zipfile.ZipFile(deck) as original, zipfile.ZipFile(changed,'w') as target:
                for info in original.infolist():
                    raw=original.read(info.filename)
                    if info.filename=='ppt/slides/slide14.xml':
                        root=E.fromstring(raw)
                        ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':A}
                        cards={s.find('p:nvSpPr/p:cNvPr',ns).get('id'):s for s in root.findall('.//p:sp',ns)}
                        x=cards['27'].find('.//a:t',ns);y=cards['37'].find('.//a:t',ns)
                        x.text,y.text=y.text,x.text
                        raw=E.tostring(root)
                    target.writestr(info,raw)
            with self.assertRaises(AssertionError):audit(changed,source)


if __name__=='__main__':unittest.main()
