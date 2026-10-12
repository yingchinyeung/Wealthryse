import copy
import unittest
from ibkr_etl.risk_execution_shape import JOB, CONTAINER, validate_risk_execution


class RiskExecutionShapeTests(unittest.TestCase):
    def wanted(self):
        return [dict(name=CONTAINER, image="registry/risk@sha256:"+"a"*64,
            command=["python"], args=["scripts/audit_risk_cache.py"],
            resources=dict(cpu=0.5, memory="1Gi"),
            env=[dict(name="DATABASE_URL", secretRef="database-url", value=""),
                 dict(name="PORTFOLIO_TENANT_ID", value="tenant")])]

    def native(self):
        value = self.wanted()
        value[0]["env"][0] = dict(name="DATABASE_URL", secretRef="cappjob-"+JOB)
        value[0]["imageType"] = "ContainerImage"
        value[0]["resources"]["ephemeralStorage"] = ""
        return value

    def test_original_and_exact_native_representations(self):
        wanted, actual = self.wanted(), self.native()
        before = copy.deepcopy(actual)
        self.assertTrue(validate_risk_execution(wanted, wanted))
        self.assertTrue(validate_risk_execution(actual, wanted))
        self.assertEqual(actual, before)

    def test_image_command_arguments_and_resources_remain_exact(self):
        for key, value in (("image", "other"), ("command", ["sh"]),
                           ("args", ["scripts/build_risk_snapshots.py"]),
                           ("resources", dict(cpu=1, memory="2Gi")),
                           ("imageType", "Function"), ("name", "other")):
            actual = self.native()
            actual[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_risk_execution(actual, self.wanted())

    def test_secret_values_aliases_and_added_environment_rejected(self):
        cases = [dict(name="DATABASE_URL",secretRef="other"),
            dict(name="DATABASE_URL",secretRef="cappjob-other"),
            dict(name="DATABASE_URL",secretRef="cappjob-"+JOB,value=""),
            dict(name="DATABASE_URL",secretRef="cappjob-"+JOB,value="sensitive"),
            dict(name="OTHER_URL",secretRef="cappjob-"+JOB)]
        for binding in cases:
            actual = self.native()
            actual[0]["env"][0] = binding
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                validate_risk_execution(actual,self.wanted())
        actual = self.native()
        actual[0]["env"].append(dict(name="NEW",value="extra"))
        with self.assertRaises(ValueError):
            validate_risk_execution(actual,self.wanted())

    def test_changed_tenant_storage_and_extra_fields_rejected(self):
        for change in ("tenant", "storage", "extra"):
            actual = self.native()
            if change == "tenant":actual[0]["env"][1]["value"] = "other"
            elif change == "storage":actual[0]["resources"]["ephemeralStorage"] = "1Gi"
            else:actual[0]["probes"] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_risk_execution(actual,self.wanted())

    def test_no_generalized_job_or_credential_acceptance(self):
        for change in ("name", "secret", "value", "duplicate"):
            wanted, actual = self.wanted(), self.native()
            if change == "name":wanted[0]["name"] = "other"
            elif change == "secret":wanted[0]["env"][0]["secretRef"] = "administrator"
            elif change == "value":wanted[0]["env"][0]["value"] = "connection"
            else:
                wanted[0]["env"].append(copy.deepcopy(wanted[0]["env"][0]))
                actual[0]["env"].append(copy.deepcopy(actual[0]["env"][0]))
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_risk_execution(actual,wanted)

    def test_container_inventory_and_environment_shape_rejected(self):
        for actual in (None, {}, [], self.native()*2, [None], [dict(name=JOB)]):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                validate_risk_execution(actual,self.wanted())


if __name__ == "__main__":
    unittest.main()
