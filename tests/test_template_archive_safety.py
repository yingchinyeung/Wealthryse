"""Synthetic archive validation; no owner templates or IBKR files are modified."""
import io
import struct
import unittest
import warnings
import zipfile
from unittest.mock import patch

from ibkr_etl.template_upload import validate_pptx_upload


def package(extra=(), compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(output, 'w', compression=compression) as archive:
            for name, content in [('[Content_Types].xml', b'<Types/>'),
                    ('ppt/presentation.xml', b'<presentation/>'),
                    ('ppt/slides/slide1.xml', b'<slide/>'), *extra]:
                archive.writestr(name, content)
    return output.getvalue()


class TemplateArchiveSafetyTests(unittest.TestCase):
    def test_standard_compression_preserves_exact_bytes(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            payload = package(compression=method)
            self.assertEqual(validate_pptx_upload('template.pptx', payload).content, payload)

    def test_duplicate_and_case_alias_parts_rejected(self):
        for name in ('ppt/slides/slide1.xml', 'PPT/SLIDES/SLIDE1.XML'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'duplicate'):
                validate_pptx_upload('template.pptx', package([(name, b'different')]))

    def test_unsafe_and_normalization_alias_paths_rejected(self):
        for name in ('/absolute', '../outside', 'ppt/../outside', 'ppt/./alias',
                'ppt//alias', 'C:/drive', 'ppt:stream'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'unsafe'):
                validate_pptx_upload('template.pptx', package([(name, b'x')]))
        # Windows ZipInfo normalizes backslashes while writing; bind the actual
        # ZIP member bytes so this exercises hostile uploads on either OS.
        for invalid in (b'ppt\\alias', b'ppt/\x00lias'):
            payload = package([('ppt/alias', b'x')]).replace(b'ppt/alias', invalid)
            with self.subTest(invalid=invalid), self.assertRaisesRegex(RuntimeError, 'unsafe'):
                validate_pptx_upload('template.pptx', payload)

    def test_unsupported_compression_rejected_before_decode(self):
        payload = package(compression=zipfile.ZIP_BZIP2)
        with patch.object(zipfile.ZipFile, 'testzip', side_effect=AssertionError('must not decode')):
            with self.assertRaisesRegex(RuntimeError, 'standard PowerPoint'):
                validate_pptx_upload('template.pptx', payload)

    def test_corrupt_contents_rejected(self):
        payload = package(compression=zipfile.ZIP_STORED).replace(b'<slide/>', b'<wrong/>')
        with self.assertRaisesRegex(RuntimeError, 'corrupt|malformed'):
            validate_pptx_upload('template.pptx', payload)

    def test_invalid_deflate_has_user_friendly_error(self):
        payload = bytearray(package())
        name_at = payload.index(b'ppt/slides/slide1.xml')
        local = payload.rfind(b'PK\x03\x04', 0, name_at)
        name_size, extra_size = struct.unpack_from('<HH', payload, local + 26)
        data_at = local + 30 + name_size + extra_size
        payload[data_at] = 0xff  # Invalid deflate block type, not a valid CRC.
        with self.assertRaisesRegex(RuntimeError, 'malformed or unreadable'):
            validate_pptx_upload('template.pptx', bytes(payload))

    def test_encrypted_flag_rejected_before_decode(self):
        payload = bytearray(package())
        local = payload.index(b'PK\x03\x04')
        central = payload.index(b'PK\x01\x02')
        for offset in (local + 6, central + 8):
            struct.pack_into('<H', payload, offset, struct.unpack_from('<H', payload, offset)[0] | 1)
        with self.assertRaisesRegex(RuntimeError, 'Encrypted'):
            validate_pptx_upload('template.pptx', bytes(payload))

    def test_bounds_rejected_before_decompression(self):
        payload = package()
        for bound in ('MAX_PRESENTATION_EXPANDED_BYTES', 'MAX_PRESENTATION_ARCHIVE_ENTRIES'):
            with self.subTest(bound=bound), patch('ibkr_etl.template_upload.' + bound, 1):
                with patch.object(zipfile.ZipFile, 'testzip', side_effect=AssertionError('must not decode')):
                    with self.assertRaisesRegex(RuntimeError, 'safety limit|too many'):
                        validate_pptx_upload('template.pptx', payload)


if __name__ == '__main__':
    unittest.main()
