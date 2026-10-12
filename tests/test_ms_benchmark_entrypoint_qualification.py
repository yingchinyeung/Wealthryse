import copy
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import qualify_ms_benchmark_entrypoint as entrypoint


class NativeEntrypointQualification(unittest.TestCase):
    def test_fixture_checks_normal_command_without_database_or_market_calls(self):
        code=entrypoint.CODE[len(entrypoint.r.CHECK):]
        code=code.replace("'/app/scripts/refresh_provisional_benchmark.py'",
                          repr(str(entrypoint.p.ROOT/'scripts/refresh_provisional_benchmark.py')))
        with redirect_stdout(io.StringIO()),patch('ibkr_etl.db.connect',side_effect=AssertionError('Real database prohibited')):
            namespace={'os':__import__('os'),'provisional_benchmark':__import__('ibkr_etl.provisional_benchmark',fromlist=['*'])}
            exec(compile(code,'entrypoint-private-fixture','exec'),namespace)
        self.assertEqual(namespace['events'],['alpha-world-ms','U12345678','success','closed'])

    def test_null_normalization_does_not_change_credential_or_command_values(self):
        template={'containers':[{'image':'candidate-digest','command':['python'],'args':['-c',entrypoint.CODE],
            'env':[{'name':'MS_ROLLBACK_KIND','value':'benchmark'}]}],
            'volumes':None,'initContainers':None}
        cleaned=entrypoint.r.old.clean(copy.deepcopy(template))
        self.assertEqual(cleaned['containers'],template['containers'])
        self.assertEqual(set(cleaned),{'containers'})

