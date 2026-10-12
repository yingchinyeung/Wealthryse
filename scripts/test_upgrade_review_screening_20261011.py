import copy
import unittest
from retain_upgrade_review_screening_20261011 import adjudicate, GENERIC

class ScreeningGuards(unittest.TestCase):
    def record(self):
        rows=[]
        for subject in sorted(GENERIC)+['alpha-world-ms','alpha-world-t']:
            for month in range(21):
                rows.append({'kind':'pdf','subject':subject,'period_end':str(month),
                             'pages':[{'nonempty':True,'text_outside_page':0,'unexpected_placeholder':False,
                                       'page_label_present':subject not in GENERIC,'page':p} for p in (1,2)]})
                rows.append({'kind':'pptx','slides':20,'screen_passed':True})
        return {'results':rows}
    def test_only_template_footer_exception(self):
        self.assertEqual(adjudicate(self.record())['other_screening_failures'],0)
    def test_clipped_text_not_waived(self):
        r=self.record();r['results'][0]['pages'][0]['text_outside_page']=1
        with self.assertRaises(ValueError):adjudicate(r)
    def test_placeholder_not_waived(self):
        r=self.record();r['results'][0]['pages'][0]['unexpected_placeholder']=True
        with self.assertRaises(ValueError):adjudicate(r)
    def test_alpha_footer_not_waived(self):
        r=self.record();r['results'][-2]['pages'][0]['page_label_present']=False
        with self.assertRaises(ValueError):adjudicate(r)
    def test_incomplete_batch_rejected(self):
        r=self.record();r['results'].pop()
        with self.assertRaises(ValueError):adjudicate(r)
    def test_package_error_not_waived(self):
        r=self.record();r['results'][1]['screen_passed']=False
        with self.assertRaises(ValueError):adjudicate(r)
    def test_duplicate_generic_month_rejected(self):
        r=self.record();r['results'][2]=copy.deepcopy(r['results'][0])
        with self.assertRaises(ValueError):adjudicate(r)

if __name__=='__main__':unittest.main()
