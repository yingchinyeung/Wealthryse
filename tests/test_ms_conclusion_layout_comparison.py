from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import compare_ms_cloud_conclusion_layout as subject


class ConclusionLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def deck(self, name, scale, text='Same conclusion', width=100):
        path = self.folder / name
        xml = (f'<a:root xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
               f'<a:ext cx="{width}" cy="100"/><a:bodyPr><a:normAutofit fontScale="{scale}"/>'
               f'</a:bodyPr><a:t>{text}</a:t></a:root>')
        with ZipFile(path,'w') as archive:
            archive.writestr('ppt/slides/slide17.xml',xml)
        return path

    def test_explains_only_known_scale_difference_without_approving_visuals(self):
        old = self.deck('old.pptx','96875')
        new = self.deck('new.pptx','100000')
        self.assertEqual(subject.compare(new,old)[0]['accepted_font_scale'],'96875')

    def test_changed_text_blocks(self):
        old = self.deck('old.pptx','96875')
        new = self.deck('new.pptx','100000',text='Different conclusion')
        with self.assertRaisesRegex(ValueError,'text, geometry'):
            subject.compare(new,old)

    def test_changed_geometry_blocks(self):
        old = self.deck('old.pptx','96875')
        new = self.deck('new.pptx','100000',width=99)
        with self.assertRaisesRegex(ValueError,'text, geometry'):
            subject.compare(new,old)

    def test_unknown_scale_blocks(self):
        old = self.deck('old.pptx','50000')
        new = self.deck('new.pptx','100000')
        with self.assertRaisesRegex(ValueError,'Unexplained autofit'):
            subject.compare(new,old)
