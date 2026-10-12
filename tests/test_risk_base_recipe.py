import copy
from pathlib import Path
import unittest
from ibkr_etl.risk_base_recipe import SOURCE, trixie_security_recipe


class RiskBaseRecipeTests(unittest.TestCase):
    def setUp(self):
        self.template=Path('Dockerfile.risk.minimal').read_text()
        self.resolution={'source':SOURCE,'amd64_digest':'sha256:'+'a'*64,
            'index_digest':'sha256:'+'b'*64,'manifest':{'schemaVersion':2},
            'index':{'manifests':[{'platform':{'architecture':'amd64','os':'linux'},'digest':'sha256:'+'a'*64}]}}

    def test_native_stable_security_upgrade_preserves_export_contract(self):
        result=trixie_security_recipe(self.template,self.resolution)
        self.assertIn('slim-trixie@sha256:'+'a'*64,result)
        self.assertIn('apt-get dist-upgrade -y',result)
        self.assertIn('ge 3.46.1-7+deb13u2',result)
        self.assertIn('ge 3.5.7-1~deb13u3',result)
        self.assertIn('VERSION_CODENAME=trixie',result)
        self.assertIn('FROM scratch',result)
        self.assertIn('USER 10001:10001',result)
        self.assertIn('RUN python scripts/assemble_minimal_risk_runtime.py',result)
        self.assertNotIn('sources.list',result)
        self.assertNotIn('ignore-unfixed',result)
        self.assertEqual(self.resolution['manifest'],{'schemaVersion':2})

    def test_mutable_wrong_distribution_and_injected_base_rejected(self):
        for field,value in (('source',SOURCE.replace('trixie','sid')),('source','python:latest'),
                ('amd64_digest','latest'),('amd64_digest','sha256:'+'a'*64+'\nRUN bad'),
                ('index_digest',None)):
            bad=copy.deepcopy(self.resolution);bad[field]=value
            with self.assertRaises(ValueError):trixie_security_recipe(self.template,bad)

    def test_missing_mixed_and_ambiguous_platforms_rejected(self):
        for change in ('missing','duplicate','wrongdigest','wrongarch','wrongschema'):
            bad=copy.deepcopy(self.resolution)
            if change=='missing':bad['index']['manifests']=[]
            elif change=='duplicate':bad['index']['manifests']*=2
            elif change=='wrongdigest':bad['index']['manifests'][0]['digest']='sha256:'+'c'*64
            elif change=='wrongarch':bad['index']['manifests'][0]['platform']['architecture']='arm64'
            else:bad['manifest']['schemaVersion']=1
            with self.assertRaises(ValueError):trixie_security_recipe(self.template,bad)

    def test_modified_template_and_extra_stages_rejected(self):
        for bad in (self.template.replace('slim-bookworm','slim-trixie'),
                self.template+'\nFROM python:latest\n',self.template.replace(' AS builder',' AS unreviewed'),
                self.template.replace('RUN python scripts/assemble_minimal_risk_runtime.py','RUN unreviewed')):
            with self.assertRaises(ValueError):trixie_security_recipe(bad,self.resolution)


if __name__=='__main__':unittest.main()
