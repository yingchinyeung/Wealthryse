"""Relationship renaming is neutral; actual content and targets are not."""
import io,unittest,zipfile
from qualify_upgrade_legacy_render_20261010 import parts


def package(rid='R1',target='media/image1.png',text='Example',reference=None,media=b'image',created='2026-01-01',duplicate=False):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w') as z:
        rel=f'<Relationship Id="{rid}" Type="image" Target="{target}"/>'
        z.writestr('ppt/_rels/slide.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+rel+(rel if duplicate else '')+'</Relationships>')
        z.writestr('ppt/slide.xml',f'<s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><i r:embed="{reference or rid}"/><t>{text}</t></s>')
        z.writestr('ppt/media/image1.png',media)
        z.writestr('docProps/core.xml',f'<core xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:created>{created}</dcterms:created><title>Retained</title></core>')
    return buf.getvalue()


class EqualityTests(unittest.TestCase):
    def test_arbitrary_relationship_names(self):self.assertEqual(parts(package()),parts(package(rid='Rrandom')))
    def test_core_creation_time(self):self.assertEqual(parts(package()),parts(package(created='2026-02-02')))
    def test_target_changes(self):self.assertNotEqual(parts(package()),parts(package(target='media/other.png')))
    def test_text_changes(self):self.assertNotEqual(parts(package()),parts(package(text='Different')))
    def test_media_changes(self):self.assertNotEqual(parts(package()),parts(package(media=b'different')))
    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):parts(package(duplicate=True))
    def test_dangling_reference_rejected(self):
        with self.assertRaises(ValueError):parts(package(reference='missing'))


if __name__=='__main__':unittest.main()
