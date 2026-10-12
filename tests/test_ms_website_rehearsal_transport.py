import base64
import gzip
import unittest
import zlib
from unittest.mock import patch

import run_ms_website_hkd_rehearsal_v6 as rehearsal


class RehearsalTransport(unittest.TestCase):
    def test_bounded_azure_arguments_recover_exact_source_without_expansions(self):
        original='data = '+repr('$('+('x'*250000)+')')+'\n'
        encoded=base64.b64encode(zlib.compress(original.encode(),9)).decode()
        job={'properties':{'template':{'containers':[{'name':'fixture','env':[]}]}}}
        args=rehearsal.runtime_template(job,encoded)['containers'][0]['args']
        self.assertTrue(all(len(a.encode())<100000 for a in args))
        self.assertTrue(all('$' not in a and '`' not in a for a in args[2:]))
        # Decode the outer wrapper and run only harmless assignment fixture code.
        namespace={}
        with patch('sys.argv',['-c',*args[2:]]):
            exec(args[1],namespace)
        self.assertEqual(namespace['data'],'$('+('x'*250000)+')')

    def test_no_payload_or_truncated_payload_is_not_accepted(self):
        encoded=base64.b64encode(zlib.compress(b'fixture = True')).decode()
        job={'properties':{'template':{'containers':[{'env':[]}]}}}
        args=rehearsal.runtime_template(job,encoded)['containers'][0]['args']
        for payload in ([],[encoded[:5]]):
            with self.subTest(payload=payload),patch('sys.argv',['-c',*payload]),self.assertRaises(Exception):
                exec(args[1],{})
