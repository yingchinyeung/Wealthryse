import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
from ibkr_etl import web

class UpgradeUITests(unittest.TestCase):
    def html(self):
        return web._render_account_page({'email':'synthetic@example.invalid','role':'owner'},
            account_id='alpha-world-t',section='trades',access={
                'display_name':'Alpha World T','reporting_currency':'HKD',
                'history_start_date':'2025-01-20','preview_mode':False})

    def test_pct_rounding_retains_real_losses(self):
        node=os.environ.get('NODE_BINARY') or shutil.which('node')
        self.assertTrue(node, 'Node must run the actual rendered formatting function')
        definition=re.search(r'^const pct=.*$',self.html(),re.M).group(0)
        checks="""
const assert=require('node:assert/strict');
assert.equal(pct(-0.000001),'0%');
assert.equal(pct(-0),'0%');
assert.equal(pct(-0.0001),'-0.01%');
assert.equal(pct(-0.00005),'-0.01%');
assert.equal(pct(0.1234),'12.34%');
assert.equal(pct(null),'—');
assert.equal(pct(NaN),'—');
assert.equal(pct(-Infinity),'—');
"""
        result=subprocess.run([node,'-e',definition+checks],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_derived_trade_controls_and_history_are_truthful(self):
        html=self.html()
        self.assertIn('Derived portfolio — no independently executed trades',html)
        self.assertIn("document.querySelector('#trade-filter-controls').hidden=true",html)
        self.assertNotIn('<div class="value">IBKR executions</div>',html)
        self.assertIn("document.querySelector('#trade-source-note').textContent=result.message",html)

    def test_burnin_label_does_not_claim_static_date_is_current(self):
        self.assertIn('Original earliest completion',web.SYSTEM_HEALTH_PAGE_SCRIPT)
        self.assertIn('Original earliest completion',self.html())
        self.assertNotIn("['Earliest completion',",self.html())

if __name__=='__main__':unittest.main()
