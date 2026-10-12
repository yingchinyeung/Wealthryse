import unittest
from prepare_reporting_agent_consumers_20261011 import transform


class PatchTests(unittest.TestCase):
    old = 'def _sector_allocation():\n    return 1\n'
    new = 'def _sector_allocation():\n    return 2\n'

    def test_preserves_other_functions_and_imports(self):
        source = 'import math\n\n' + self.old + '\ndef _risk():\n    return "consumer-specific"\n'
        self.assertEqual(transform(source, self.old, self.new), source.replace('return 1', 'return 2'))

    def test_different_sector_fails(self):
        with self.assertRaises(ValueError):
            transform(self.new, self.old, self.new)

    def test_missing_sector_fails(self):
        with self.assertRaises(ValueError):
            transform('pass\n', self.old, self.new)

    def test_duplicate_sector_fails(self):
        with self.assertRaises(ValueError):
            transform(self.old + self.old, self.old, self.new)

    def test_decorated_sector_fails(self):
        with self.assertRaises(ValueError):
            transform('@cache\n' + self.old, self.old, self.new)

    def test_other_qualified_function_not_copied(self):
        self.assertEqual(transform(self.old, self.old, self.new + '\ndef _risk():\n    return "unrelated"\n'), self.new)


if __name__ == '__main__':
    unittest.main()
