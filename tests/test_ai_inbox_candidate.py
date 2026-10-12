import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_ai_inbox_candidate as candidate


class CandidateTests(unittest.TestCase):
    def test_context_has_no_operational_payloads_or_hidden_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('src/module.py','src/.secret.py','scripts/worker.py','scripts/raw/statement.py',
                         'tests/test_worker.py','tests/__pycache__/cache.py','tests/statement.pdf',
                         '.env','templates/design.pdf','reports/factsheet.pdf'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
            self.assertEqual(candidate.source_paths(root),['Dockerfile','Dockerfile.azure','Dockerfile.risk','pyproject.toml','requirements.lock','scripts/worker.py','src/module.py','tests/test_worker.py'])

    def test_candidate_preserves_base_assets_and_runs_locked_tests(self):
        docker=(candidate.ROOT/'deploy/ai-inbox.Dockerfile').read_text()
        self.assertIn('FROM ${VERIFIED_BASE_IMAGE}',docker)
        self.assertIn('--require-hashes',docker)
        self.assertIn('python -m unittest discover',docker)
        for forbidden in ('COPY templates','COPY . ','ENTRYPOINT','CMD [','latest'):
            self.assertNotIn(forbidden,docker)

    def test_arbitrary_xml_cannot_replace_reviewed_synthetic_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/candidate.FIXTURE;path.parent.mkdir(parents=True)
            path.write_bytes(b'not the reviewed fictional fixture')
            with self.assertRaises(ValueError):candidate.verify_fixture(root)


if __name__=='__main__':unittest.main()
