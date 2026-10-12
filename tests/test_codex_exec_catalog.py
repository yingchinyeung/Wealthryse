"""Catalog alias copies native metadata without inventing model capabilities."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from extract_codex_exec_catalog import alias_catalog, canonical, NATIVE_MODEL, ALIAS
import prepare_codex_exec_probe as prepare


class CodexExecCatalogTests(unittest.TestCase):
    def test_exact_native_entry_is_copied_with_only_alias_and_upgrade_changed(self):
        native = dict(slug=NATIVE_MODEL, upgrade={'to': 'other'}, base_instructions='public fixture',
                      context_window=272000, tool_capabilities=['fixture'])
        bundled = dict(models=[native, dict(slug='unrelated')]); before = copy.deepcopy(bundled)
        catalog, digest = alias_catalog(bundled)
        self.assertEqual(bundled, before)
        self.assertEqual(catalog, {'models': [{**native, 'slug': ALIAS, 'upgrade': None}]})
        self.assertEqual(digest, hashlib.sha256(canonical(native)).hexdigest())

    def test_missing_duplicate_or_invalid_native_entry_is_rejected(self):
        for value in (None, [], {}, {'models': None}, {'models': []},
                      {'models': [{'slug': NATIVE_MODEL}, {'slug': NATIVE_MODEL}]}):
            with self.assertRaises(ValueError):
                alias_catalog(value)

    def test_vendored_public_catalog_hash_and_single_alias_are_pinned(self):
        content = (ROOT/'deploy/codex-exec-model-catalog.json').read_bytes()
        descriptor = json.loads((ROOT/'deploy/codex-exec-catalog-source.json').read_text())
        self.assertEqual(hashlib.sha256(content).hexdigest(), descriptor['catalog_sha256'])
        self.assertEqual(descriptor['catalog_sha256'], '0c231c9cce20499aabf693206989a84cb0552d83bb6af80ec1ea471bfeb282bb')
        value = json.loads(content)
        self.assertEqual(len(value['models']), 1)
        self.assertEqual(value['models'][0]['slug'], ALIAS)
        self.assertIsNone(value['models'][0]['upgrade'])
        self.assertIs(descriptor['inference_called'], False)
        self.assertIs(descriptor['credentials_included'], False)

    def test_linux_runner_independently_compares_native_entry_and_alias(self):
        text = (ROOT/'scripts/run_codex_exec_probe.py').read_text()
        self.assertIn("'debug', 'models', '--bundled'", text)
        self.assertIn("native_catalog_metadata_differs", text)
        self.assertIn("custom_alias_metadata_differs", text)
        self.assertIn('6d1d665f8d03d6dfb18f51d87a510712a0f72fda44423538896b8e1c59427413', text)

    def test_catalog_descriptor_and_extractor_are_part_of_frozen_source(self):
        files, record = prepare.manifest()
        self.assertIn('catalog-source.json', files)
        self.assertIn('codex-exec-model-catalog.json', files)
        self.assertIn('scripts/extract_codex_exec_catalog.py', record['helpers'])


if __name__ == '__main__': unittest.main()
