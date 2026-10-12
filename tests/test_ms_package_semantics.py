from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from compare_ms_package_semantics import compare, REL


class PackageSemanticsTests(unittest.TestCase):
    def make(self, path, identifier, timestamp='2026-10-08T00:00:00Z', target='/ppt/media/image.png', text='HKD', image=b'original', reference=None):
        with ZipFile(path, 'w') as archive:
            archive.writestr('ppt/slides/_rels/slide1.xml.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="{identifier}" Type="image" Target="{target}" /></Relationships>')
            archive.writestr('ppt/slides/slide1.xml', f'<slide xmlns:r="{REL}" r:embed="{reference or identifier}"><text>{text}</text></slide>')
            archive.writestr('docProps/core.xml', f'<core xmlns:d="http://purl.org/dc/terms/"><d:created>{timestamp}</d:created></core>')
            archive.writestr('ppt/media/image.png', image)

    def test_only_resolved_ids_and_generation_dates_may_vary(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder)/'a.pptx', Path(folder)/'b.pptx'
            self.make(first, 'first')
            self.make(second, 'second', timestamp='2026-10-08T03:00:00.123Z')
            self.assertEqual(compare(first, second), [])

    def test_content_relationship_target_and_binary_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder)/'a.pptx', Path(folder)/'b.pptx'
            self.make(first, 'first')
            for mutation in ({'target':'https://unapproved.example/'}, {'text':'USD'}, {'image':b'changed'}):
                with self.subTest(mutation=mutation):
                    self.make(second, 'second', **mutation)
                    self.assertTrue(compare(first, second))

    def test_dangling_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder)/'a.pptx', Path(folder)/'b.pptx'
            self.make(first, 'first')
            self.make(second, 'second', reference='missing')
            with self.assertRaises(ValueError):
                compare(first, second)


if __name__ == '__main__':
    unittest.main()
