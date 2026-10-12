import copy,unittest
from verify_viewer_private_admin_extension_20261008 import check_retained,p,repair,owner_role_matches

class ExtensionReadbackTests(unittest.TestCase):
    def test_role_scope_case_only_not_scope_or_principal_changes(self):
        vm=p.rid('Microsoft.Compute/virtualMachines',p.VM)
        self.assertTrue(owner_role_matches([{'principalId':p.OWNER,'scope':vm.lower()}],vm))
        for roles in ([{'principalId':'other','scope':vm}],[{'principalId':p.OWNER,'scope':p.BASE}],[],[{'principalId':p.OWNER,'scope':vm}]*2):
            self.assertFalse(owner_role_matches(roles,vm))
    def fixture(self):
        vm=p.rid('Microsoft.Compute/virtualMachines',p.VM)
        before={vm:{'properties':{'secureBoot':True}},'other-new-resource':{'network':'restricted'}}
        wanted={'publisher':'Microsoft.Azure.ActiveDirectory','type':'AADSSHLoginForLinux','typeHandlerVersion':'1.0','autoUpgradeMinorVersion':True}
        after=copy.deepcopy(before);after[vm]['resources']=[{'id':repair.EXT,'type':'Microsoft.Compute/virtualMachines/extensions','properties':copy.deepcopy(wanted)}]
        return before,after,wanted,vm
    def test_only_exact_new_child_is_accepted(self):
        before,after,wanted,vm=self.fixture();check_retained(before,after,wanted)
        for alter in ('additional-child','vm-security','different-child','automatic-upgrade'):
            bad=copy.deepcopy(after)
            if alter=='additional-child':bad[vm]['resources'].append(copy.deepcopy(bad[vm]['resources'][0]))
            elif alter=='vm-security':bad[vm]['properties']['secureBoot']=False
            elif alter=='different-child':bad[vm]['resources'][0]['id']='another-extension'
            else:bad[vm]['resources'][0]['properties']['enableAutomaticUpgrade']=True
            with self.assertRaises(ValueError):check_retained(before,bad,wanted)

if __name__=='__main__':unittest.main()
