import unittest
from capture_reporting_consumers_offline_20261011 import apply_layer, member_name


class OverlayTests(unittest.TestCase):
    def test_replace(self):
        self.assertEqual(apply_layer({'app/a': b'old'}, {'app/a': b'new'}, set(), set()), {'app/a': b'new'})

    def test_whiteout(self):
        self.assertEqual(apply_layer({'app/a': b'a', 'app/b': b'b'}, {}, {'app/a'}, set()), {'app/b': b'b'})

    def test_directory_whiteout(self):
        self.assertEqual(apply_layer({'app/src/a': b'a', 'app/scripts/b': b'b'}, {}, {'app/src'}, set()), {'app/scripts/b': b'b'})

    def test_opaque_preserves_current_layer(self):
        self.assertEqual(apply_layer({'app/src/a': b'a'}, {'app/src/b': b'b'}, set(), {'app/src'}), {'app/src/b': b'b'})

    def test_root_opaque(self):
        self.assertEqual(apply_layer({'app/a': b'a'}, {}, set(), {''}), {})

    def test_safe_normalization(self):
        self.assertEqual(member_name('./app/src/a.py'), 'app/src/a.py')

    def test_unsafe_path(self):
        for name in ('app/../secret', 'app\\secret'):
            with self.assertRaises(ValueError):
                member_name(name)


if __name__ == '__main__':
    unittest.main()
