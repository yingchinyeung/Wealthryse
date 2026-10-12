import unittest
from unittest.mock import Mock
from ibkr_etl.agent_gateway_limits import GatewayAdmission,AdmissionDenied


class AdmissionTests(unittest.TestCase):
    def test_one_active_request_without_queue_and_slot_recovery(self):
        limiter=GatewayAdmission()
        with limiter.admit('a'):
            with self.assertRaisesRegex(AdmissionDenied,'busy'):
                with limiter.admit('b'): pass
        with limiter.admit('b'): pass
        with self.assertRaises(ValueError):
            with limiter.admit('a'): raise ValueError('tool failure')
        with limiter.admit('a'): pass

    def test_per_user_rate_and_window_expiry(self):
        clock=Mock(return_value=10);limiter=GatewayAdmission(per_minute=2,clock=clock)
        for _ in range(2):
            with limiter.admit('a'): pass
        with self.assertRaisesRegex(AdmissionDenied,'rate_limit'):
            with limiter.admit('a'): pass
        with limiter.admit('b'): pass
        clock.return_value=70
        with limiter.admit('a'): pass
        self.assertNotIn('a',limiter._buckets)

    def test_bounded_identity_inventory_and_fail_closed_clock(self):
        clock=Mock(return_value=1);limiter=GatewayAdmission(max_principals=1,clock=clock)
        with limiter.admit('a'): pass
        with self.assertRaisesRegex(AdmissionDenied,'capacity'):
            with limiter.admit('b'): pass
        clock.return_value=0
        with self.assertRaisesRegex(AdmissionDenied,'clock'):
            with limiter.admit('a'): pass
        clock.return_value=float('nan')
        with self.assertRaisesRegex(AdmissionDenied,'clock'):
            with limiter.admit('a'): pass

    def test_invalid_config_and_principal(self):
        for value in (True,0,-1,10000):
            with self.assertRaises(ValueError): GatewayAdmission(concurrency=value)
        for value in (None,'','a'*257):
            with self.assertRaises(AdmissionDenied):
                with GatewayAdmission().admit(value): pass
