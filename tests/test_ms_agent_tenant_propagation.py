import unittest
from unittest.mock import MagicMock,patch
from ibkr_etl import agent_tools as tools

class TenantPropagation(unittest.TestCase):
    def setUp(self):
        self.context=tools.AgentToolContext('verified-tenant','actor',frozenset({'alpha-world-ms'}),'test')
        self.executor=MagicMock(return_value=tools.AgentToolResult('get_risk_metrics','alpha-world-ms','2026-10-07','warn',{},(),''))
        self.definition=tools.AgentToolDefinition('get_risk_metrics','',tools._subject_schema(),self.executor)
    def call(self,args):
        with patch.dict(tools.TOOL_REGISTRY,{'get_risk_metrics':self.definition}):
            return tools.execute_tool(MagicMock(),self.context,'get_risk_metrics',args)
    def test_passes_verified_tenant_after_contract(self):
        with patch.object(tools,'require_ms_hkd_contract') as contract:
            self.call({'subject_id':'alpha-world-ms'})
        self.assertEqual(contract.call_args.kwargs['tenant_id'],'verified-tenant')
        self.assertEqual(self.executor.call_args.args[3],{'subject_id':'alpha-world-ms','_verified_tenant_id':'verified-tenant'})
    def test_user_cannot_inject_tenant(self):
        with self.assertRaises(tools.AgentToolError) as raised:
            self.call({'subject_id':'alpha-world-ms','_verified_tenant_id':'foreign'})
        self.assertEqual(raised.exception.code,'unexpected_arguments');self.executor.assert_not_called()
    def test_wrong_subject_still_denied(self):
        with self.assertRaises(tools.AgentToolError) as raised:self.call({'subject_id':'alpha-world-t'})
        self.assertEqual(raised.exception.status_code,403);self.executor.assert_not_called()
    def test_contract_failure_never_calls_executor(self):
        with patch.object(tools,'require_ms_hkd_contract',side_effect=ValueError('mismatch')):
            with self.assertRaises(tools.AgentToolError) as raised:self.call({'subject_id':'alpha-world-ms'})
        self.assertEqual(raised.exception.code,'ms_hkd_contract_not_ready');self.executor.assert_not_called()
if __name__=='__main__':unittest.main()
