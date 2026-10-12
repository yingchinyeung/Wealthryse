import copy
import unittest
from ibkr_etl.fx_evidence_contract import validate_version
from ibkr_etl.parser import _source_key
from ibkr_etl.util import canonical_json,sha256_text


def fixture(attrs=None):
    attrs = attrs or {"accountId":"A","fromCurrency":"HKD","toCurrency":"USD","rate":"0.128","reportDate":"20250120"}
    return dict(account_id="A",section="ConversionRates",record_type="ConversionRate",report_date="2025-01-20",
        canonical_key=_source_key("A","ConversionRates","ConversionRate",attrs,effective_report_date="2025-01-20"),
        row_sha256=sha256_text(canonical_json(attrs)),data_json=attrs)


class FxEvidenceContractTests(unittest.TestCase):
    def test_valid_original_attributes_are_copied(self):
        row=fixture()
        result=validate_version(row,account_id="A")
        self.assertEqual(row,result)
        result["data_json"]["rate"]="other"
        self.assertEqual(row["data_json"]["rate"],"0.128")

    def test_hash_and_key_must_both_match(self):
        for change in ({"row_sha256":"f"*64},{"canonical_key":"fx-rate:v2:"+"e"*64}):
            with self.assertRaises(ValueError):
                validate_version({**fixture(),**change},account_id="A")

    def test_original_attribute_account_is_checked(self):
        row=fixture()
        attrs={**row["data_json"],"accountId":"B"}
        with self.assertRaises(ValueError):
            validate_version(fixture(attrs),account_id="A")
        with self.assertRaises(ValueError):
            validate_version(row,account_id="B")

    def test_source_date_cannot_be_silently_changed(self):
        row=fixture()
        row["report_date"]="2025-01-21"
        row["canonical_key"]=_source_key("A",row["section"],row["record_type"],row["data_json"],effective_report_date=row["report_date"])
        with self.assertRaises(ValueError):
            validate_version(row,account_id="A")

    def test_invalid_rate_preserved_but_missing_currency_rejected(self):
        attrs={**fixture()["data_json"],"rate":"0"}
        self.assertEqual(validate_version(fixture(attrs),account_id="A")["data_json"]["rate"],"0")
        del attrs["fromCurrency"]
        with self.assertRaises(ValueError):
            validate_version(fixture(attrs),account_id="A")

    def test_unknown_fields_types_and_nonstring_values_rejected(self):
        variants=[{**fixture(),"extra":"value"},{**fixture(),"record_type":"Trade"}]
        row=copy.deepcopy(fixture())
        row["data_json"]["rate"]=0.128
        variants.append(row)
        for row in variants:
            with self.assertRaises(ValueError):
                validate_version(row,account_id="A")

    def test_statement_date_fallback_and_currency_aliases_supported(self):
        row=fixture({"currency":"HKD","baseCurrency":"USD","exchangeRate":"0.128"})
        self.assertEqual(validate_version(row,account_id="A"),row)
