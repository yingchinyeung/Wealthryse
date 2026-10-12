import ast, base64, gzip, lzma, unittest
from inspect_upgrade_owner_payloads_20261010 import decode, literal


class StaticTests(unittest.TestCase):
    def test_plain_no_execution(self):
        code = "raise RuntimeError('never execute')"
        self.assertEqual(decode(code), [code])

    def test_gzip_base64(self):
        code = 'import json\nvalue=123\n'
        packed = base64.b64encode(gzip.compress(code.encode())).decode()
        text = "import base64,gzip;exec(gzip.decompress(base64.b64decode(" + repr(packed) + ")))"
        self.assertIn(code, decode(text))

    def test_lzma_base85_dollar_escape(self):
        code = 'import ssl\nvalue=456\n'
        packed = base64.b85encode(lzma.compress(code.encode())).decode().replace('$', ':')
        text = "import base64,lzma;exec(lzma.decompress(base64.b85decode(" + repr(packed) + ".replace(':',chr(36)))))"
        self.assertIn(code, decode(text))

    def test_no_eval_unknown_expression(self):
        for expression in ("open('secret').read()", "__import__('os').environ['TOKEN']", "'a'.replace('a','b')"):
            with self.assertRaises(ValueError): literal(ast.parse(expression, mode='eval').body)

    def test_recursive_embedded_source(self):
        child = '# synthetic source\n' * 30 + 'import ssl\n'
        self.assertIn(child, decode('SOURCE=' + repr(child)))

    def test_size_and_depth_fail_closed(self):
        with self.assertRaises(ValueError): decode('x' * 4000001)
        with self.assertRaises(ValueError): decode('x=1', depth=7)

    def test_invalid_archive_rejected(self):
        with self.assertRaises(ValueError): decode("base64.b64decode('**bad**')")

    def test_compressed_bomb_bounded(self):
        packed = base64.b64encode(gzip.compress(b'x' * 4000001)).decode()
        with self.assertRaises(ValueError): decode('base64.b64decode(' + repr(packed) + ')')


if __name__ == '__main__': unittest.main()
