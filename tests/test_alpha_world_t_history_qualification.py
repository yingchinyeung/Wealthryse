"""Fail-closed controls for the private real-data T reconciliation utility."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from qualify_alpha_world_t_history import independent_values, qualify


def observations():
    return {
        'source_nav':[{'subject_id':a,'snapshot_date':day,'currency':'USD','total_text':str(v)}
            for a,values in [('U16088606',(100,110)),('U16088571',(200,240))]
            for day,v in zip(('2025-01-20','2025-01-21'),values)],
        'broker_fx':[{'account_id':'U16088606','rate_date':day,'from_currency':'HKD',
                      'to_currency':'USD','rate_text':rate}
            for day,rate in [('2025-01-20','0.125'),('2025-01-21','0.1')]],
        'market_prices':[{'instrument_id':instrument,'price_date':day,'price':v}
            for instrument,values in [('hang-seng-price-index',(100,120)),('spy-settlement',(100,110))]
            for day,v in zip(('2025-01-20','2025-01-21'),values)],
    }


class THistoryQualificationTests(unittest.TestCase):
    def test_full_account_component_conversion_precedes_normalization(self):
        values=independent_values(observations())
        self.assertEqual(values['2025-01-20'],Decimal(100))
        # Full GC +37.5%, US +50%, HSI +20%, SPY +37.5% in HKD.
        self.assertEqual(values['2025-01-21'],Decimal(58)+Decimal(91)*Decimal('1.375')+
            Decimal(45)*Decimal('1.5')-Decimal(49)*Decimal('1.2')-Decimal(45)*Decimal('1.375'))

    def test_duplicate_missing_mixed_currency_or_invalid_fx_fail(self):
        changes=(lambda d:d['source_nav'].append(deepcopy(d['source_nav'][0])),
                 lambda d:d['source_nav'].pop(),
                 lambda d:d['source_nav'][0].update(currency='HKD'),
                 lambda d:d['broker_fx'][0].update(rate_text='0'),
                 lambda d:d['broker_fx'].clear())
        for change in changes:
            data=observations(); change(data)
            with self.subTest(change=change),self.assertRaises(ValueError): independent_values(data)

    def test_hash_mismatch_creates_no_database_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'source.json'; source.write_text(json.dumps(observations()))
            target=root/'qualification'
            with self.assertRaisesRegex(ValueError,'hash differs'):
                qualify(source,'0'*64,target)
            self.assertFalse(target.exists())


if __name__=='__main__': unittest.main()
