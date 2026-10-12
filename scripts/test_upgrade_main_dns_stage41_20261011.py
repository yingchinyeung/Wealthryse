import dis
import unittest
from unittest.mock import patch
import control_upgrade_main_dns_stage41_v2_20261011 as c

class CurrentDNSGuards(unittest.TestCase):
    def test_captured_boundary_independent_of_rebound_export(self):
        function=c.current_boundary_function()
        # Rebinding the controller export cannot turn its boundary into recursive
        # calls to the wrapper. The compiled function retains the actual guards.
        operands=[i.argval for i in dis.get_instructions(function)]
        self.assertIn(32,operands)
        self.assertNotIn(34,operands)
        self.assertIn('workloads',function.__code__.co_consts)
        self.assertIn('Public rollback path must remain enabled',function.__code__.co_consts)
        with patch.object(c.original,'boundary',lambda:None):
            self.assertIn(32,[i.argval for i in dis.get_instructions(function)])
    def test_unknown_stage_denied(self):
        with self.assertRaises(ValueError):c.main('delete-everything')
    def test_frozen_sources_include_controller_and_tests(self):
        names=c.sources()
        self.assertIn('test_upgrade_main_dns_stage41_20261011.py',names)
        self.assertEqual(len(names),4)
    def test_rebinding_does_not_change_original_file(self):
        before=c.sources()['control_upgrade_main_dns_20261011.py']
        c.current_boundary_function()
        self.assertEqual(before,c.sources()['control_upgrade_main_dns_20261011.py'])

if __name__=='__main__':unittest.main()
