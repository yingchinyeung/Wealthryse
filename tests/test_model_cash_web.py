"""Authorized model cash route and unchanged broker-cash behavior."""
import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi import HTTPException
from ibkr_etl import web


class ModelCashWebTests(unittest.TestCase):
    def setUp(self):
        self.user={'email':'viewer@example.invalid','role':'viewer'}
        self.access={'subject_type':'composite_portfolio','reporting_currency':'HKD','history_start_date':'2025-01-20'}
        self.guard=self.enterContext(patch.object(web,'_portfolio_access_context',return_value=self.access))
        self.db=self.enterContext(patch.object(web,'_database'));self.conn=self.db.return_value.__enter__.return_value
        self.model={'as_of':'2026-09-30','normalized_value':58.,'nav_weight':.4,'message':'Model only'}
        self.loader=self.enterContext(patch.object(web,'load_model_cash',return_value=self.model))
    def call(self,subject='alpha-world-t',**kw):return web.cash_activity(subject,as_of='2026-09-30',user=self.user,**kw)
    def test_model_cash_is_separate_from_broker_balances(self):
        result=self.call();self.assertEqual(result['model_cash'],self.model);self.assertFalse(result['activity_applicable']);self.assertEqual(result['balances'],[]);self.assertEqual(result['transactions'],[])
        self.loader.assert_called_once_with(self.conn,tenant_id=web.TENANT_ID,subject_id='alpha-world-t',reporting_currency='HKD',as_of='2026-09-30')
    def test_ms_uses_same_reader(self):self.assertEqual(self.call('alpha-world-ms')['model_cash'],self.model)
    def test_authorization_precedes_database(self):
        self.guard.side_effect=HTTPException(status_code=403,detail='Denied')
        with self.assertRaises(HTTPException):self.call()
        self.db.assert_not_called();self.loader.assert_not_called()
    def test_metadata_mismatch_fails_closed(self):
        self.access['subject_type']='physical_account'
        with self.assertRaises(HTTPException) as error:self.call()
        self.assertEqual(error.exception.status_code,409);self.loader.assert_not_called()
    def test_invalid_components_withheld(self):
        self.loader.side_effect=ValueError('Synthetic reconciliation failed')
        with self.assertRaises(HTTPException) as error:self.call()
        self.assertEqual(error.exception.status_code,409)
    def test_no_model_observation_not_zero_cash(self):
        self.loader.return_value=None;result=self.call();self.assertIsNone(result['model_cash']);self.assertFalse(result['activity_applicable']);self.assertIsNone(result['balances_date'])
    def test_physical_cash_unchanged(self):
        self.access['subject_type']='physical_account';self.conn.execute.return_value.fetchone.return_value={'latest':'2026-09-30'}
        self.conn.execute.return_value.fetchall.side_effect=[[{'currency':'USD','ending_cash_text':'100'}],[{'amount_text':'5'}]]
        result=self.call('U16088571');self.assertEqual(result['balances'][0]['ending_cash_text'],'100');self.assertEqual(result['total_count'],1);self.loader.assert_not_called()
    def test_display_units_and_export_boundary(self):
        text=Path(web.__file__).read_text(encoding='utf-8');self.assertIn('Model cash (normalized NAV units)',text);self.assertIn('Share of current model NAV',text);self.assertIn("result.activity_applicable===false",text)


if __name__=='__main__':unittest.main()
