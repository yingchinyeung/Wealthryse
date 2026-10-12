from pathlib import Path
import unittest

from ibkr_etl.cloud_storage import content_type_for_path


class CloudStorageContentTypeTests(unittest.TestCase):
    def test_published_artifact_content_types(self):
        expected = {
            "dashboard.html": "text/html; charset=utf-8",
            "manifest.json": "application/json; charset=utf-8",
            "factsheet.pdf": "application/pdf",
            "presentation.pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "snapshot.sqlite": "application/vnd.sqlite3",
            "exports.zip": "application/zip",
            "statement.xml": "application/xml",
            "unknown.data": "application/octet-stream",
        }
        for filename, content_type in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(content_type_for_path(Path(filename)), content_type)


if __name__ == "__main__":
    unittest.main()
