import copy
import unittest

from ibkr_etl.alpha_world_ms_hkd import rebuild
from ibkr_etl.subject_onboarding import approved_reference_definitions, configuration_sha256


def inspection():
    contract=approved_reference_definitions()['alpha-world-ms']
    contract['base_currency']='USD'
    result={'contracts':[{'subject_id':'alpha-world-ms','approval_status':'approved',
        'configuration_json':contract,'configuration_sha256':configuration_sha256(contract)}],
        'components':[],'nav':[],'broker_fx':[]}
    for day,rate in [('2025-01-20',7.8),('2025-01-21',7.9)]:
        result['broker_fx'].append({'account_id':'U16088571','from_currency':'USD',
            'to_currency':'HKD','rate_date':day,'rate_text':str(rate)})
        contributions=[]
        for spec in contract['components']:
            value=100 if day.endswith('20') else 110
            contribution=100*spec['coefficient']*(1 if day.endswith('20') else 1.1)
            contributions.append(contribution)
            result['components'].append(dict(snapshot_date=day,observation_date=day,
                component_id=spec['component_id'],source_currency=spec['source_currency'],
                source_value=value,coefficient=spec['coefficient'],contribution=contribution))
        result['components'].append(dict(snapshot_date=day,observation_date=day,
            component_id='constant-cash',source_currency='USD',source_value=58,coefficient=1,contribution=58))
        result['nav'].append({'snapshot_date':day,'currency':'USD','total_nav':58+sum(contributions)})
    return result


class AlphaMsHkdTests(unittest.TestCase):
    def test_converts_sleeves_before_normalization_and_preserves_inputs(self):
        source=inspection();before=copy.deepcopy(source)
        result=rebuild(source,cutoff='2025-01-21')
        expected=58+100*1.1*((.91+.45-.45)*7.9/7.8-.49)
        self.assertAlmostEqual(result['series'][1]['nav'],expected)
        self.assertEqual(result['series'][0]['nav'],100)
        self.assertNotAlmostEqual(result['series'][1]['nav'],source['nav'][1]['total_nav']*7.9/7.8)
        self.assertEqual(source,before)

    def test_rejects_duplicate_unapproved_drifted_and_future_sources(self):
        changes=(lambda s:s['components'].append(dict(s['components'][0])),
            lambda s:s['contracts'][0].update(approval_status='draft'),
            lambda s:s['contracts'][0]['configuration_json'].update(constant_cash=59),
            lambda s:s['components'][5].update(observation_date='2025-01-22'),
            lambda s:s['components'][5].update(source_value=float('nan')),
            lambda s:s['components'][5].update(contribution=999))
        for change in changes:
            with self.subTest(change=change):
                source=inspection();change(source)
                with self.assertRaises(ValueError):rebuild(source,cutoff='2025-01-21')


if __name__=='__main__':unittest.main()
