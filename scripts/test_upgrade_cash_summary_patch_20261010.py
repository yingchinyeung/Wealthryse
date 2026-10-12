import unittest
from prepare_upgrade_cash_summary_20261010 import BASE,OLD,NEW,transform


class DisplayPatchTests(unittest.TestCase):
    def test_exact_one_line_change(self):
        before=(BASE/'context/src/ibkr_etl/web.py').read_text(encoding='utf-8')
        after=transform(before)
        self.assertEqual(after.replace(NEW,OLD),before)
        self.assertEqual(len([1 for a,b in zip(before.splitlines(),after.splitlines()) if a!=b]),1)
    def test_no_currency_is_invented(self):
        self.assertIn('currency code not supplied',NEW)
        self.assertNotIn('USD',NEW);self.assertNotIn('HKD',NEW)
    def test_amounts_and_other_currency_escape_preserved(self):
        for value in ('fmt(x.ending_cash_text)','fmt(x.settled_cash_text)','esc(x.currency)'):
            self.assertIn(value,NEW)
        self.assertNotIn('parseFloat',NEW)
    def test_drift_and_duplicate_rejected(self):
        for value in ('unrelated',OLD+'\n'+OLD):
            with self.subTest(value=value),self.assertRaises(ValueError):transform(value)


if __name__=='__main__':unittest.main()
