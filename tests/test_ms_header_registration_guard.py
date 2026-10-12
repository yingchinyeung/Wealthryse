"""Pure tests for the new metadata-only registration mutation boundary."""
import ast
from pathlib import Path
import unittest


class HeaderRegistrationGuardTests(unittest.TestCase):
    def setUp(self):
        source=Path(__file__).resolve().parents[1]/'scripts/register_ms_headers_runtime_v14r15.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        self.scope={}
        selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('require','verify_state')]
        exec(compile(ast.Module(body=selected,type_ignores=[]),str(source),'exec'),self.scope)
        self.before={'subjects':[{'lifecycle_status':'active'}],'approvals':[{'status':'approved'}],
            'batches':[{'status':'approved'}],'snapshots':[{'data_sha256':'original'}],
            'runs':[{'generation_run_id':1,'template_version':'old','report_type':'factsheet','status':'awaiting_approval'}]}
        import copy
        self.after=copy.deepcopy(self.before)
        self.after['runs'] += [{'generation_run_id':i+2,'template_version':'new','report_type':'factsheet','status':'awaiting_approval'} for i in range(21)]

    def test_exact_21_private_factsheet_additions_allowed(self):
        self.scope['verify_state'](self.before,self.after,'new')

    def test_original_approvals_subjects_batches_snapshots_immutable(self):
        import copy
        for key in ('subjects','approvals','batches','snapshots'):
            changed=copy.deepcopy(self.after);changed[key]=[]
            with self.subTest(key=key),self.assertRaises(ValueError):self.scope['verify_state'](self.before,changed,'new')

    def test_prior_runs_cannot_be_rewritten(self):
        self.after['runs'][0]['status']='released_internal'
        with self.assertRaises(ValueError):self.scope['verify_state'](self.before,self.after,'new')

    def test_missing_extra_released_or_presentation_additions_rejected(self):
        import copy
        for mutation in ('missing','extra','released','presentation'):
            changed=copy.deepcopy(self.after)
            if mutation=='missing':changed['runs'].pop()
            elif mutation=='extra':changed['runs'].append(copy.deepcopy(changed['runs'][-1]))
            elif mutation=='released':changed['runs'][-1]['status']='released_internal'
            else:changed['runs'][-1]['report_type']='presentation'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.scope['verify_state'](self.before,changed,'new')


if __name__=='__main__':unittest.main()
