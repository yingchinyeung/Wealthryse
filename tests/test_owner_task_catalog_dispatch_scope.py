"""No live effects: corrected filenames and exact retained-preflight lineage."""
import unittest
import owner_task_catalog_dispatch_scope as s


class ScopeTests(unittest.TestCase):
    def test_exact_two_additions_preserve_all_834_sources(self):
        before={str(i):'a'*64 for i in range(834)};after=dict(before,**{k:'b'*64 for k in s.EXTRAS})
        s.extension(before,after)
        for changed in (dict(after,**{'0':'c'*64}),dict(after,extra='d'*64)):
            with self.assertRaises(ValueError):s.extension(before,changed)

    def test_dispatch_references_new_marker_not_consumed_old_marker(self):
        class Marker:
            def __init__(self,path,seen):self.path,self.seen=path,seen
            def exists(self):self.seen.append(self.path);return True
        class Folder:
            def __init__(self):self.seen=[]
            def __truediv__(self,path):return Marker(path,self.seen)
        from types import SimpleNamespace
        folder=Folder()
        namespace=dict(s.base.control(),p=SimpleNamespace(old=SimpleNamespace(FOLDER=folder)))
        fn=s.corrected_dispatch(namespace)
        with self.assertRaises(ValueError):fn(None,'job')
        self.assertEqual(folder.seen,[s.base.NAME_PREFIX+'job-intent.json'])
        self.assertNotIn('serviceaud-step3-fence-job-intent.json',folder.seen)

    def test_corrected_dispatch_can_pass_old_marker_without_effects(self):
        from types import SimpleNamespace
        class Marker:
            def exists(self):return False
        class Folder:
            def __truediv__(self,path):
                self_test.assertTrue(path.startswith(s.base.NAME_PREFIX));return Marker()
        self_test=self
        n=dict(s.base.control(),p=SimpleNamespace(old=SimpleNamespace(FOLDER=Folder())))
        class StopBeforeEffect(Exception):pass
        def load(name):self.assertEqual(name,'live-preflight');raise StopBeforeEffect()
        n['load']=load
        with self.assertRaises(StopBeforeEffect):s.corrected_dispatch(n)(None,'job')

    def test_predecessor_context_and_plan_not_retargeted(self):
        old,guard=s.predecessor_context()
        self.assertEqual(old['worker'].JOB,s.base.prior.candidate.JOB)
        self.assertEqual(guard.__globals__['catalog_job'],s.base.worker.JOB)
        n=s.control();self.assertEqual(n['JOB'],s.base.worker.JOB_ID)
        self.assertEqual(s.BASE_SHA,'9e32fd1de374a07e3ca2158f0b1e4b0bdee53202ee4b197a66429b0add20869b')


if __name__=='__main__':unittest.main()
