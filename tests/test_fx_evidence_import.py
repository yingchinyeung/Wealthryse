import copy
import hashlib
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from psycopg import sql
from psycopg.pq import TransactionStatus
from ibkr_etl.fx_evidence_bundle import build_bundle
from ibkr_etl.fx_evidence_import import fingerprint,stage_evidence,validate_bundle,read_bundle,_stage_group
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.util import canonical_json

TENANT="00000000-0000-0000-0000-000000000001"


def fixture():
    content=b'<FlexStatement accountId="A" toDate="20250120"><ConversionRates><ConversionRate fromCurrency="HKD" toCurrency="USD" rate="0.128"/></ConversionRates></FlexStatement>'
    metadata=dict(tenant_id=TENANT,run_id=10,status="success",completed_at="2025-01-21T00:00:00Z",raw_sha256=hashlib.sha256(content).hexdigest())
    row=next(r for r in parse_flex_xml(content).rows if r.record_type=="ConversionRate")
    legacy=dict(tenant_id=TENANT,account_id="A",data_json=canonical_json(row.attributes),row_sha256=row.row_sha256,
        section=row.section,record_type=row.record_type,source_key="legacy",report_date=row.report_date,first_seen_run_id=9,last_seen_run_id=10)
    return dict(tenant_id=TENANT,account_id="A",source_namespace="v11-trial",source_snapshot_sha256="a"*64,
        archives=[(metadata,content)],legacy_rows=[legacy])


class FxEvidenceImportTests(unittest.TestCase):
    def test_batched_readback_is_order_and_chunk_size_independent(self):
        scope={"tenant_id":TENANT,"account_id":"A","source_namespace":"trial"}
        rows=[{"source_run_id":i,"archive_sha256":str(i)*64} for i in (5,1,4,2,3)]
        ordered=sorted(rows,key=canonical_json)
        expected=[{**scope,**r} for r in ordered]
        for size in (1,2,5):
            c=MagicMock()
            c.execute.return_value.fetchall.side_effect=[
                [dict(r) for r in reversed(expected[start:start+size])]
                for start in range(0,len(rows),size)]
            actual,inserted,queries=_stage_group(c,table=sql.Identifier("trial","runs"),scope=scope,
                columns=(*scope,"source_run_id","archive_sha256"),keys=("source_run_id",),
                rows=rows,group="test",prior=True,batch_size=size)
            self.assertEqual(actual,expected)
            self.assertEqual(inserted,0)
            self.assertEqual(queries,(len(rows)+size-1)//size)
            for call in c.execute.call_args_list:
                self.assertEqual(call.args[1][:3],tuple(scope.values()))
                self.assertIn('"tenant_id"=%s AND "account_id"=%s',call.args[0].as_string())

    def test_batched_readback_rejects_same_count_wrong_content(self):
        scope={"tenant_id":TENANT,"account_id":"A"}
        rows=[{"source_run_id":i,"archive_sha256":"a"*64} for i in (1,2)]
        c=MagicMock()
        c.execute.return_value.fetchall.return_value=[{**scope,**r,"archive_sha256":"b"*64} for r in rows]
        with self.assertRaisesRegex(ValueError,"Evidence conflict"):
            _stage_group(c,table=sql.Identifier("trial","runs"),scope=scope,
                columns=(*scope,"source_run_id","archive_sha256"),keys=("source_run_id",),
                rows=rows,group="test",prior=True,batch_size=2)
        self.assertEqual(c.execute.call_count,1)

    def test_batch_size_limits_fail_before_database_access(self):
        b=build_bundle(**fixture());c=MagicMock()
        for size in (0,-1,2001,True,1.5):
            with self.assertRaises(ValueError):stage_evidence(c,b,expected_sha256=b["bundle_sha256"],batch_size=size)
        c.cursor.assert_not_called()

    def test_artifact_read_pins_bytes_bundle_and_account(self):
        b=build_bundle(**fixture());content=canonical_json(b).encode()
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"bundle.json";p.write_bytes(content)
            options=dict(expected_artifact_sha256=hashlib.sha256(content).hexdigest(),expected_bundle_sha256=b["bundle_sha256"],tenant_id=TENANT,account_id="A")
            self.assertEqual(read_bundle(p,**options),b)
            with self.assertRaises(ValueError):read_bundle(p,**{**options,"account_id":"B"})
            p.write_bytes(content+b" ")
            with self.assertRaises(ValueError):read_bundle(p,**options)

    def test_duplicate_json_members_are_rejected(self):
        b=build_bundle(**fixture());content=('{"account_id":"B",'+canonical_json(b)[1:]).encode()
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"bundle.json";p.write_bytes(content)
            with self.assertRaises(ValueError):
                read_bundle(p,expected_artifact_sha256=hashlib.sha256(content).hexdigest(),expected_bundle_sha256=b["bundle_sha256"],tenant_id=TENANT,account_id="A")

    def test_archive_assembly_is_deterministic_and_binds_alias(self):
        bundle=build_bundle(**fixture())
        self.assertEqual(bundle,build_bundle(**fixture()))
        self.assertEqual(bundle["aliases"][0]["legacy_first_seen_run_id"],9)
        self.assertEqual(len(bundle["versions"]),1)

    def test_wrong_raw_digest_rejected(self):
        f=fixture()
        f["archives"][0][0]["raw_sha256"]="f"*64
        with self.assertRaises(ValueError):build_bundle(**f)

    def test_run_tenant_and_success_verified(self):
        for change in ({"tenant_id":"other"},{"status":"failed"},{"completed_at":None}):
            f=fixture();f["archives"][0][0].update(change)
            with self.assertRaises(ValueError):build_bundle(**f)

    def test_alias_cannot_reference_missing_observation(self):
        f=fixture();f["legacy_rows"][0]["last_seen_run_id"]=999
        with self.assertRaises(ValueError):build_bundle(**f)

    def test_tampered_bundle_and_external_digest_rejected(self):
        b=build_bundle(**fixture());original=b["bundle_sha256"]
        b["versions"][0]["data_json"]["rate"]="9"
        with self.assertRaises(ValueError):validate_bundle(b,expected_sha256=original)
        b["bundle_sha256"]=fingerprint(b)
        with self.assertRaises(ValueError):validate_bundle(b,expected_sha256=b["bundle_sha256"])

    def test_duplicate_observation_and_unreferenced_runs_rejected(self):
        for group in ("runs","observations"):
            b=build_bundle(**fixture());b[group].append(copy.deepcopy(b[group][0]));b["bundle_sha256"]=fingerprint(b)
            with self.assertRaises(ValueError):validate_bundle(b,expected_sha256=b["bundle_sha256"])
        b=build_bundle(**fixture());b["runs"].append({**b["runs"][0],"source_run_id":11});b["bundle_sha256"]=fingerprint(b)
        with self.assertRaises(ValueError):validate_bundle(b,expected_sha256=b["bundle_sha256"])

    def test_refuses_absent_outer_transaction_before_database_access(self):
        b=build_bundle(**fixture());c=MagicMock();c.info.transaction_status=TransactionStatus.IDLE
        with self.assertRaises(ValueError):stage_evidence(c,b,expected_sha256=b["bundle_sha256"])
        c.cursor.assert_not_called();c.commit.assert_not_called()

    def test_refuses_wrong_tenant_before_inserts(self):
        b=build_bundle(**fixture());c=MagicMock();c.info.transaction_status=TransactionStatus.INTRANS
        cur=c.cursor.return_value.__enter__.return_value
        cur.execute.return_value.fetchone.return_value={"tenant":"other"}
        with self.assertRaises(ValueError):stage_evidence(c,b,expected_sha256=b["bundle_sha256"])
        self.assertEqual(cur.execute.call_count,1);c.commit.assert_not_called()
