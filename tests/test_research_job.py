"""The research job, its storage and its deployment files (docs/RESEARCH_ZONE.md).

No Azure, network or database: the feature export runs on in-memory sources, the blob store
on a fake container client, the server control on a fake Resource Manager, and the Bicep and
role files are checked as text. Synthetic data only. tests/test_research_roles_db.py runs the
role scripts against a real PostgreSQL.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import math
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ibkr_etl.research import blob_store, experiment_log, feature_store, signals
from ibkr_etl.research.point_in_time import PointInTimeView

ROOT = Path(__file__).resolve().parents[1]
BICEP = ROOT / "infra" / "research.bicep"
HAVE_PYARROW = importlib.util.find_spec("pyarrow") is not None
UTC = timezone.utc
ACCOUNT, OTHER_ACCOUNT = "U9100001", "U9100002"
_SCRIPTS: dict = {}


def script(name: str):
    if name not in _SCRIPTS:
        spec = importlib.util.spec_from_file_location(f"research_{name}", ROOT / "scripts" / "research" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look their module up while the class is made
        spec.loader.exec_module(module)
        _SCRIPTS[name] = module
    return _SCRIPTS[name]


def job():
    return script("run_research_job")


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


# --- synthetic bronze --------------------------------------------------------------------------

def _element(tag: str, **attributes: str) -> str:
    return f"<{tag} " + " ".join(f'{key}="{value}"' for key, value in attributes.items()) + " />"


def statement(account: str, to_date: str, *, positions=(), instruments=()) -> bytes:
    parts = [f'<FlexStatement accountId="{account}" fromDate="{to_date}" toDate="{to_date}" period="Custom">',
             _element("AccountInformation", accountId=account, currency="USD"), "<FinancialInstrumentInformation>"]
    parts += [_element("FinancialInstrument", accountId=account, **row) for row in instruments]
    parts += ["</FinancialInstrumentInformation>", "<OpenPositions>"]
    parts += [_element("OpenPosition", accountId=account, reportDate=to_date, **row) for row in positions]
    parts += ["</OpenPositions>", "</FlexStatement>"]
    return ('<FlexQueryResponse queryName="Synthetic" type="AF"><FlexStatements count="1">'
            + "".join(parts) + "</FlexStatements></FlexQueryResponse>").encode()


def chart(symbol: str, closes: dict[str, float]) -> bytes:
    """A Yahoo chart response; each bar is stamped at the New York open."""
    stamps = [int(datetime.fromisoformat(f"{day}T13:30:00+00:00").timestamp()) for day in closes]
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"symbol": symbol, "currency": "usd", "exchangeTimezoneName": "America/New_York"},
        "timestamp": stamps, "indicators": {"quote": [{"close": list(closes.values())}]}}]}}).encode()


def worked_example() -> feature_store.MemorySilverReader:
    """docs/MEDALLION_AS_KNOWN_ON.md: the 15 Oct sale re-priced and its NAV restated on 17 Oct."""
    first, second = at(16, 8, 21), at(17, 8, 21)
    nav = [
        {"account_id": ACCOUNT, "snapshot_date": "2026-10-15", "total_text": "100500.00", "recorded_from": first,
         "recorded_to": second},
        {"account_id": ACCOUNT, "snapshot_date": "2026-10-15", "total_text": "100480.00", "recorded_from": second,
         "recorded_to": None},
        {"account_id": OTHER_ACCOUNT, "snapshot_date": "2026-10-15", "total_text": "7.00", "recorded_from": first,
         "recorded_to": None},
    ]
    trades = [
        {"account_id": ACCOUNT, "trade_date": "2026-10-15", "source_key": "sale", "proceeds_text": "5000.00",
         "recorded_from": first, "recorded_to": None},
        {"account_id": ACCOUNT, "trade_date": "2026-10-15", "source_key": "cancellation", "proceeds_text": "-5000.00",
         "recorded_from": second, "recorded_to": None},
        {"account_id": ACCOUNT, "trade_date": "2026-10-15", "source_key": "rebooking", "proceeds_text": "4980.00",
         "recorded_from": second, "recorded_to": None},
    ]
    return feature_store.MemorySilverReader({"nav_snapshots": nav, "trades": trades})


def bronze() -> feature_store.MemoryBronzeSource:
    position = dict(currency="USD", assetCategory="STK", symbol="SYNA", conid="9001", position="100",
                    costBasisMoney="4000.00", fifoPnlUnrealized="1000.00")
    friday = statement(ACCOUNT, "20261015", positions=[dict(position, markPrice="50.00", positionValue="5000.00")],
                       instruments=[dict(conid="9001", symbol="SYNA", description="SYNTHETIC A", assetCategory="STK",
                                         currency="USD", listingExchange="NYSE")])
    saturday = statement(ACCOUNT, "20261015", positions=[dict(position, markPrice="49.80", positionValue="4980.00")],
                         instruments=[dict(conid="9001", symbol="SYNA", description="", isin="US0000000017")])
    stranger = statement(OTHER_ACCOUNT, "20261015", positions=[dict(position, conid="9002", symbol="SYNB",
                                                                    markPrice="1.00", positionValue="100.00")])
    return feature_store.MemoryBronzeSource(
        statement_files=[
            feature_store.Capture("ibkr-raw", "t1/2026/10/16/flex_a.xml", at(16, 8, 20), friday),
            feature_store.Capture("ibkr-raw", "t1/2026/10/16/flex_b.xml", at(16, 8, 20), stranger),
            feature_store.Capture("ibkr-raw", "t1/2026/10/17/flex_c.xml", at(17, 8, 20), saturday),
        ],
        market_files={"yahoo-chart": [
            feature_store.Capture("bronze-market-data", "yahoo-chart/2026/10/16/a.json", at(16, 21, 30),
                                  chart("SYNA", {"2026-10-15": 50.0, "2026-10-16": 51.0})),
            feature_store.Capture("bronze-market-data", "yahoo-chart/2026/10/17/b.json", at(17, 21, 30),
                                  chart("SYNA", {"2026-10-16": 51.5})),
        ]})


def export(as_known_on: str, sink=None, **changes):
    request = feature_store.ExportRequest(as_known_on=as_known_on, subjects=(ACCOUNT,),
                                          tenant_id="00000000-0000-0000-0000-000000000001", run_id="research-test",
                                          **changes)
    sink = sink if sink is not None else feature_store.MemorySink()
    result = feature_store.export_features(request, silver=worked_example(), bronze=bronze(), sink=sink,
                                           write_parquet=HAVE_PYARROW, now=at(18, 12))
    return result, sink


def manifest(sink, dataset: str, as_known_on: str) -> dict:
    return json.loads(sink.read(f"{dataset}/{as_known_on}/{ACCOUNT}/manifest.json"))


def rows_of(sink, dataset: str, as_known_on: str) -> list[dict]:
    """The rows as exported: from the Parquet file, or (without pyarrow) rebuilt and checked against the manifest."""
    held = manifest(sink, dataset, as_known_on)
    if HAVE_PYARROW:
        return feature_store.read_parquet(sink.read(held["file"]["path"]))
    raise unittest.SkipTest("pyarrow is not installed, so the exported rows are checked through their sha256")


# --- tests -------------------------------------------------------------------------------------

class ArgumentTests(unittest.TestCase):
    def parse(self, *argv):
        with contextlib.redirect_stderr(io.StringIO()):
            return job().build_parser().parse_args(list(argv))

    def test_export_takes_a_date_and_repeated_subjects(self):
        args = self.parse("export-features", "--as-known-on", "2026-11-06", "--subject", ACCOUNT, "--subject",
                          OTHER_ACCOUNT, "--dataset", "market_prices", "--local-dir", "/tmp/x")
        self.assertEqual((args.command, args.as_known_on, args.subject, args.dataset),
                         ("export-features", "2026-11-06", [ACCOUNT, OTHER_ACCOUNT], ["market_prices"]))
        self.assertEqual(args.local_dir, Path("/tmp/x"))
        self.assertFalse(args.keep_server_running)
        self.assertFalse(args.include_v11_archive)

    def test_required_arguments_and_choices_are_enforced(self):
        for argv in (("export-features", "--subject", ACCOUNT), ("export-features", "--as-known-on", "2026-11-06"),
                     ("export-features", "--as-known-on", "2026-11-06", "--subject", ACCOUNT, "--dataset", "gold"),
                     ("backtest",), ("delete-everything",), ()):
            with self.subTest(argv), self.assertRaises(SystemExit):
                self.parse(*argv)

    def test_server_commands(self):
        self.assertTrue(self.parse("stop-server", "--if-idle").if_idle)
        self.assertFalse(self.parse("stop-server").if_idle)
        self.assertEqual(self.parse("start-server").command, "start-server")
        self.assertTrue(self.parse("backtest", "--spec", "s.json", "--keep-server-running").keep_server_running)


class FeatureExportTests(unittest.TestCase):
    def test_silver_is_read_as_known_at_the_end_of_the_day(self):
        friday, friday_sink = export("2026-10-16", datasets=("nav_snapshots", "trades"))
        saturday, saturday_sink = export("2026-10-17", datasets=("nav_snapshots", "trades"))
        # The website showed 100,500.00 on 16 Oct; the restatement is known from 17 Oct 08:21.
        self.assertEqual(friday["datasets"]["nav_snapshots"]["rows"], 1)
        self.assertEqual(friday["datasets"]["trades"]["rows"], 1)
        self.assertEqual(saturday["datasets"]["trades"]["rows"], 3)
        friday_rows = [{"account_id": ACCOUNT, "snapshot_date": "2026-10-15", "total_text": "100500.00"}]
        saturday_rows = [{"account_id": ACCOUNT, "snapshot_date": "2026-10-15", "total_text": "100480.00"}]
        columns = ["account_id", "snapshot_date", "total_text"]
        self.assertEqual(manifest(friday_sink, "nav_snapshots", "2026-10-16")["rows_sha256"],
                         feature_store.rows_sha256(friday_rows, columns))
        self.assertEqual(manifest(saturday_sink, "nav_snapshots", "2026-10-17")["rows_sha256"],
                         feature_store.rows_sha256(saturday_rows, columns))
        held = manifest(friday_sink, "nav_snapshots", "2026-10-16")
        self.assertEqual(held["columns"], columns)
        self.assertEqual(held["known_at_cutoff"], "2026-10-16T23:59:59.999999+00:00")
        self.assertEqual(held["sources"], [{"kind": "silver-as-known", "function": "silver.nav_snapshots_as_known",
                                            "at": "2026-10-16T23:59:59.999999+00:00", "accounts": [ACCOUNT]}])
        self.assertEqual(held["subjects"], [ACCOUNT])  # the other account's NAV is not exported

    def test_positions_are_replayed_in_the_order_they_were_stored(self):
        friday, friday_sink = export("2026-10-16", datasets=("positions",))
        saturday, saturday_sink = export("2026-10-17", datasets=("positions",))
        self.assertEqual(friday["datasets"]["positions"]["rows"], 1)  # the other account's statement is left out
        expected = {"account_id": ACCOUNT, "snapshot_date": "2026-10-15", "conid": "9001", "model_code": "",
                    "currency": "USD", "quantity_text": "100", "cost_basis_text": "4000.00",
                    "mark_price_text": "49.80", "market_value_text": "4980.00", "unrealized_pnl_text": "1000.00"}
        rows = feature_store.replay_positions(bronze().statement_files, feature_store.cutoff_for("2026-10-17"), [ACCOUNT])
        self.assertEqual(len(rows), 1)
        self.assertEqual({key: rows[0][key] for key in expected}, expected)
        self.assertEqual(rows[0]["known_at"], at(17, 8, 20))
        self.assertEqual(rows[0]["source_blob"], "t1/2026/10/17/flex_c.xml")
        earlier = feature_store.replay_positions(bronze().statement_files, feature_store.cutoff_for("2026-10-16"),
                                                 [ACCOUNT])
        self.assertEqual(earlier[0]["mark_price_text"], "50.00")
        source = manifest(saturday_sink, "positions", "2026-10-17")["sources"][0]
        self.assertEqual((source["kind"], source["container"], source["captures"]), ("bronze-replay", "ibkr-raw", 3))
        self.assertEqual(manifest(friday_sink, "positions", "2026-10-16")["sources"][0]["captures"], 2)

    def test_instrument_details_fold_like_the_loader(self):
        rows = feature_store.replay_instruments(bronze().statement_files, feature_store.cutoff_for("2026-10-17"),
                                                [ACCOUNT])
        self.assertEqual([row["conid"] for row in rows], ["9001"])
        row = rows[0]
        # Saturday's empty description keeps Friday's; its ISIN is new.
        self.assertEqual((row["symbol"], row["description"], row["isin"], row["listing_exchange"]),
                         ("SYNA", "SYNTHETIC A", "US0000000017", "NYSE"))
        self.assertEqual((row["first_known_at"], row["last_known_at"]), (at(16, 8, 20), at(17, 8, 20)))
        friday = feature_store.replay_instruments(bronze().statement_files, feature_store.cutoff_for("2026-10-16"),
                                                  [ACCOUNT])
        self.assertIsNone(friday[0]["isin"])

    def test_market_prices_take_the_latest_stored_close(self):
        friday = feature_store.replay_market_prices(bronze().market_files["yahoo-chart"],
                                                    feature_store.cutoff_for("2026-10-16"))
        saturday = feature_store.replay_market_prices(bronze().market_files["yahoo-chart"],
                                                      feature_store.cutoff_for("2026-10-17"))
        self.assertEqual([(r["symbol"], r["price_date"], r["close"], r["currency"]) for r in friday],
                         [("SYNA", "2026-10-15", 50.0, "USD"), ("SYNA", "2026-10-16", 51.0, "USD")])
        self.assertEqual([(r["price_date"], r["close"]) for r in saturday], [("2026-10-15", 50.0), ("2026-10-16", 51.5)])
        self.assertEqual(saturday[1]["known_at"], at(17, 21, 30))

    def test_the_export_manifest_and_its_fingerprint(self):
        result, sink = export("2026-10-17")
        self.assertEqual(result["manifest"], f"manifests/2026-10-17/{ACCOUNT}.json")
        self.assertEqual(set(result["datasets"]), set(feature_store.DATASETS))
        held = json.loads(sink.read(result["manifest"]))
        digests = {name: item["rows_sha256"] for name, item in sorted(held["datasets"].items())}
        self.assertEqual(held["fingerprint"],
                         experiment_log.data_fingerprint("feature-export", "2026-10-17", [ACCOUNT], digests))
        self.assertEqual(result["datasets"]["fx_rates"]["rows"], 0)  # no history rows: an empty, recorded dataset
        for name, item in held["datasets"].items():
            dataset = manifest(sink, name, "2026-10-17")
            self.assertEqual((dataset["rows"], dataset["rows_sha256"]), (item["rows"], item["rows_sha256"]), name)
            self.assertEqual(dataset["schema"], feature_store.SCHEMA)
            if HAVE_PYARROW:
                data = sink.read(dataset["file"]["path"])
                self.assertEqual(dataset["file"]["sha256"], __import__("hashlib").sha256(data).hexdigest())
            else:
                self.assertIsNone(dataset["file"])
                self.assertFalse(any(path.endswith(".parquet") for path in sink.files))

    def test_parquet_rows_read_back(self):
        _, sink = export("2026-10-17", datasets=("market_prices",))
        rows = rows_of(sink, "market_prices", "2026-10-17")
        self.assertEqual([(r["price_date"], r["close"]) for r in rows], [("2026-10-15", 50.0), ("2026-10-16", 51.5)])
        export_, loaded = feature_store.load_dataset(sink, f"manifests/2026-10-17/{ACCOUNT}.json", "market_prices")
        self.assertEqual(loaded, rows)

    def test_running_again_keeps_the_files_and_a_different_result_stops(self):
        first, sink = export("2026-10-17", datasets=("nav_snapshots",))
        before = dict(sink.files)
        again, _ = export("2026-10-17", sink=sink, datasets=("nav_snapshots",))
        self.assertEqual((first["outcome"], again["outcome"]), ("written", "existing"))
        self.assertEqual(again["datasets"]["nav_snapshots"]["outcome"], "existing")
        self.assertEqual(sink.files, before)
        changed = worked_example()
        changed.versions["nav_snapshots"][1]["total_text"] = "100470.00"
        request = feature_store.ExportRequest("2026-10-17", (ACCOUNT,), "t", "research-test", datasets=("nav_snapshots",))
        with self.assertRaises(feature_store.ExportConflict):
            feature_store.export_features(request, silver=changed, bronze=bronze(), sink=sink, write_parquet=False,
                                          now=at(18, 12))
        self.assertEqual(sink.files, before)

    def test_bad_requests_are_refused(self):
        for as_known_on, subjects, datasets in (("2026-10-18", (ACCOUNT,), ("trades",)),  # not over yet
                                                ("17/10/2026", (ACCOUNT,), ("trades",)),
                                                ("2026-10-17", ("u9100001",), ("trades",)),
                                                ("2026-10-17", (), ("trades",)),
                                                ("2026-10-17", (ACCOUNT,), ("gold_figures",))):
            request = feature_store.ExportRequest(as_known_on, subjects, "t", "r", datasets=datasets)
            with self.subTest(as_known_on=as_known_on, subjects=subjects), self.assertRaises(feature_store.ExportError):
                feature_store.export_features(request, silver=worked_example(), bronze=bronze(),
                                              sink=feature_store.MemorySink(), write_parquet=False, now=at(18, 12))

    def test_pyarrow_is_imported_only_for_parquet(self):
        text = (ROOT / "src" / "ibkr_etl" / "research" / "feature_store.py").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?m)^(import pyarrow|from pyarrow)")
        for name in ("blob_store.py", "signals.py"):
            self.assertNotIn("pyarrow", (ROOT / "src" / "ibkr_etl" / "research" / name).read_text(encoding="utf-8"))


# --- the blob store ----------------------------------------------------------------------------

class FakeDownload:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def readall(self) -> bytes:
        return self._data


class FakeBlob:
    def __init__(self, container: "FakeContainer", name: str) -> None:
        self._container, self.name = container, name

    def create_append_blob(self, match_condition=None):
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceExistsError

        self._container.calls.append(("create", self.name, match_condition))
        if self.name in self._container.blobs:
            if match_condition == MatchConditions.IfMissing:
                raise ResourceExistsError("BlobAlreadyExists")
        self._container.blobs.setdefault(self.name, b"")

    def append_block(self, data: bytes):
        if self._container.block_limit is not None and self._container.blocks >= self._container.block_limit:
            from azure.core.exceptions import HttpResponseError

            raise HttpResponseError("BlockCountExceedsLimit")
        self._container.blocks += 1
        self._container.blobs[self.name] += data

    def download_blob(self):
        from azure.core.exceptions import ResourceNotFoundError

        if self.name not in self._container.blobs:
            raise ResourceNotFoundError("BlobNotFound")
        return FakeDownload(self._container.blobs[self.name])

    def upload_blob(self, data, overwrite=False, content_settings=None):
        from azure.core.exceptions import ResourceExistsError

        if self.name in self._container.blobs and not overwrite:
            raise ResourceExistsError("BlobAlreadyExists")
        self._container.blobs[self.name] = bytes(data)


class Listed:
    def __init__(self, name, created, size, metadata=None):
        self.name, self.creation_time, self.size, self.metadata = name, created, size, metadata


class FakeContainer:
    def __init__(self, blobs=None, listing=(), block_limit=None) -> None:
        self.blobs = dict(blobs or {})
        self.listing = list(listing)
        self.calls: list = []
        self.blocks = 0
        self.block_limit = block_limit

    def get_blob_client(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def list_blobs(self, name_starts_with="", include=None):
        self.calls.append(("list", name_starts_with, include))
        return [item for item in self.listing if item.name.startswith(name_starts_with)]


class BlobStoreTests(unittest.TestCase):
    def test_appends_go_to_one_append_blob_created_once(self):
        container = FakeContainer()
        store = blob_store.AppendBlobStore(container)
        self.assertEqual(store.lines(), [])
        store.append('{"trial": 1}')
        store.append('{"trial": 2}')
        self.assertEqual(container.blobs[experiment_log.LOG_PATH], b'{"trial": 1}\n{"trial": 2}\n')
        self.assertEqual([call[0] for call in container.calls], ["create"])
        # A second writer finds the log there: If-None-Match keeps it, nothing is cleared.
        other = blob_store.AppendBlobStore(container)
        other.append('{"trial": 3}')
        self.assertEqual(other.lines(), ['{"trial": 1}', '{"trial": 2}', '{"trial": 3}'])

    def test_the_experiment_log_counts_each_trial_once(self):
        log = experiment_log.ExperimentLog(blob_store.AppendBlobStore(FakeContainer()))
        trial = experiment_log.Trial(family="f", hypothesis="h", parameters={"a": 1}, data_fingerprint="d",
                                     code_version="c", metrics={"sharpe_ratio": 0.5})
        first = log.record(trial)
        self.assertEqual(log.record(trial), first)
        log.record(experiment_log.Trial(family="f", hypothesis="h", parameters={"a": 2}, data_fingerprint="d",
                                        code_version="c", metrics={"sharpe_ratio": 1.0}))
        self.assertEqual(log.family_statistics("f")["trials"], 2)
        self.assertAlmostEqual(log.family_statistics("f")["variance"], 0.125)

    def test_bad_lines_and_a_full_log_are_refused(self):
        container = FakeContainer(block_limit=1)
        store = blob_store.AppendBlobStore(container)
        for line in ("", "two\nlines", "carriage\rreturn"):
            with self.subTest(line=line), self.assertRaises(ValueError):
                store.append(line)
        store.append("one")
        with self.assertRaises(blob_store.StorageError):
            store.append("two")
        container.blobs[experiment_log.LOG_PATH] += b"half a li"
        with self.assertRaises(blob_store.StorageError):
            store.lines()

    def test_files_are_never_replaced_unless_asked(self):
        container = FakeContainer()
        files = blob_store.BlobFiles(container)
        self.assertIsNone(files.read("reports/x.json"))
        files.write("reports/x.json", b"1", content_type="application/json", overwrite=False)
        with self.assertRaises(FileExistsError):
            files.write("reports/x.json", b"2", content_type="application/json", overwrite=False)
        files.write("reports/x.json", b"3", content_type="application/json", overwrite=True)
        self.assertEqual(files.read("reports/x.json"), b"3")

    def test_bronze_captures_count_from_when_they_were_stored(self):
        raw = FakeContainer(blobs={"t1/a.xml": b"<a/>", "t1/b.xml": b"<b/>", "t1/c.txt": b"c",
                                   "v11-archive/old.xml": b"<o/>"},
                            listing=[Listed("t1/a.xml", at(16, 8), 4), Listed("t1/b.xml", at(19, 8), 4),
                                     Listed("t1/c.txt", at(16, 8), 1), Listed("v11-archive/old.xml", at(17, 0), 4)])
        market = FakeContainer(blobs={"yahoo-chart/x.json": b"{}", "yahoo-chart/y.json": b"{}"},
                               listing=[Listed("yahoo-chart/x.json", at(16, 8), 2, {"received_at": "2026-10-16T09:00:00+00:00"}),
                                        Listed("yahoo-chart/y.json", at(16, 8), 2, {"received_at": "2026-10-18T09:00:00+00:00"})])
        source = blob_store.BlobBronzeSource(raw, market, tenant_id="t1")
        self.assertEqual([c.name for c in source.statements(known_by=at(17, 12))], ["t1/a.xml"])
        with_archive = blob_store.BlobBronzeSource(raw, market, tenant_id="t1", include_v11_archive=True)
        self.assertEqual([c.name for c in with_archive.statements(known_by=at(17, 12))],
                         ["t1/a.xml", "v11-archive/old.xml"])
        captures = list(source.market_captures("yahoo-chart", known_by=at(17, 12)))
        self.assertEqual([(c.name, c.known_at) for c in captures], [("yahoo-chart/x.json", at(16, 9))])
        self.assertIn(("list", "yahoo-chart/", ["metadata"]), market.calls)

    def test_only_the_research_containers_are_opened(self):
        for name in ("internal-reports", "report-drafts", "gold"):
            with self.subTest(name), self.assertRaises(blob_store.StorageError):
                blob_store.container(name, "https://example.invalid")
        self.assertEqual(set(blob_store.WRITE_CONTAINERS), {"research-features", "research-experiments"})
        self.assertEqual(set(blob_store.READ_ONLY_CONTAINERS), {"ibkr-raw", "bronze-market-data", "bronze-vendor-data"})


# --- the job's commands ------------------------------------------------------------------------

class FakeServer:
    def __init__(self, state: str = "Stopped") -> None:
        self.current, self.actions = state, []

    def state(self) -> str:
        return self.current

    def start(self) -> str:
        if self.current == "Stopped":
            self.actions.append("start")
            self.current = "Ready"
        return self.current

    def stop(self) -> str:
        if self.current == "Ready":
            self.actions.append("stop")
            self.current = "Stopped"
        return self.current


class FakeRegister:
    enabled = True

    def __init__(self, others: int = 0) -> None:
        self.calls: list = []
        self.others = others

    def started(self, run_id, command, code_version, detail):
        self.calls.append(("started", command))

    def finished(self, run_id, status, detail):
        self.calls.append(("finished", status))

    def export_recorded(self, run_id, export):
        self.calls.append(("export", sorted(export["datasets"])))

    def running(self, *, other_than, within_seconds):
        self.calls.append(("running", other_than, within_seconds))
        return self.others

    def check(self):
        return {"configured": True, "ok": True}


def prices(days: int = 260) -> dict[str, dict[str, float]]:
    """Four synthetic stocks on weekdays: a steady riser, a faller, a wave and a quiet one."""
    start, out, day = date(2025, 1, 6), {"RISE": {}, "FALL": {}, "WAVE": {}, "CALM": {}}, 0
    current = start
    while day < days:
        if current.weekday() < 5:
            stamp = current.isoformat()
            out["RISE"][stamp] = 100.0 * 1.0015 ** day
            out["FALL"][stamp] = 100.0 * 0.999 ** day
            out["WAVE"][stamp] = 100.0 * (1.0 + 0.05 * math.sin(day / 7.0))
            out["CALM"][stamp] = 100.0 * (1.0 + 0.002 * math.sin(day / 3.0)) * 1.0002 ** day
            day += 1
        current += timedelta(days=1)
    return out


class JobCommandTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="research-job-test-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        handler = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, handler)

    def run_main(self, argv, **context):
        context.setdefault("environ", {})
        context.setdefault("now", lambda: at(18, 12))
        built = job().Context(**context)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = job().main(argv, context=built)
        return code, json.loads(out.getvalue().strip().splitlines()[-1]), built

    def spec(self, **changes) -> Path:
        (self.folder / "prices.json").write_text(json.dumps(prices()), encoding="utf-8")
        spec = {"family": "momentum-synthetic", "hypothesis": "Recent winners keep winning for a while",
                "signal": "momentum", "parameters": {"skip": 5, "top": 2}, "grid": {"lookback": [20, 40]},
                "prices": {"path": "prices.json"},
                "backtest": {"rebalance": "monthly", "commission_bps": 5, "spread_bps": 5},
                "walk_forward": {"train": 100, "test": 50}, "report_name": "first"}
        spec.update(changes)
        path = self.folder / f"spec-{len(list(self.folder.glob('spec-*')))}.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    def test_backtest_end_to_end(self):
        local = self.folder / "out"
        code, result, _ = self.run_main(["backtest", "--spec", str(self.spec()), "--local-dir", str(local)])
        self.assertEqual(code, 0)
        self.assertEqual((result["status"], result["trials"], result["family_trials"]), ("passed", 2, 2))
        folder = local / "experiments" / "reports" / "momentum-synthetic" / "first"
        report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
        markdown = (folder / "report.md").read_text(encoding="utf-8")
        self.assertEqual([trial["settings"] for trial in report["trials"]],
                         [{"skip": 5, "top": 2, "lookback": 20}, {"skip": 5, "top": 2, "lookback": 40}])
        self.assertEqual(report["family_statistics"]["trials"], 2)
        deflated = report["deflated_sharpe"]["deflated_sharpe_ratio"]
        self.assertTrue(0.0 <= deflated <= 1.0, deflated)
        self.assertEqual(report["deflated_sharpe"]["trials"], 2)
        walk = report["walk_forward"]
        self.assertTrue(walk["available"])
        tests = [split["test"] for split in walk["splits"]]
        self.assertEqual(len(tests), len({tuple(window) for window in tests}))
        for split in walk["splits"]:
            self.assertLess(split["train"][1], split["test"][0])  # never tested on a training day
        self.assertEqual(walk["out_of_sample_days"], 50 * len(walk["splits"]))
        self.assertIn("Deflated Sharpe ratio", markdown)
        self.assertIn(job().REPORT_DISCLAIMER, markdown)
        log = (local / "experiments" / experiment_log.LOG_PATH).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 2)
        self.assertEqual(json.loads(log[0])["content"]["metrics"]["signals_version"], signals.MODEL_VERSION)

        # The same trials again add nothing to the family; a third parameter set does.
        _, again, _ = self.run_main(["backtest", "--spec", str(self.spec(report_name="second")), "--local-dir",
                                     str(local)])
        self.assertEqual(again["family_trials"], 2)
        _, wider, _ = self.run_main(["backtest", "--spec", str(self.spec(report_name="third",
                                                                         grid={"lookback": [20, 40, 60]})),
                                     "--local-dir", str(local)])
        self.assertEqual(wider["family_trials"], 3)
        # A report is never overwritten.
        with self.assertRaises(FileExistsError):
            self.run_main(["backtest", "--spec", str(self.spec()), "--local-dir", str(local)])

    def test_bad_specs_are_refused(self):
        for changes in ({"signal": "astrology"}, {"family": "Has Spaces"}, {"grid": {"lookback": []}},
                        {"prices": {"path": "p.json", "export_manifest": "m.json"}}, {"surprise": 1},
                        {"backtest": {"leverage": 3}}, {"walk_forward": {"train": 100, "test": 50, "step": 10}}):
            with self.subTest(changes), self.assertRaises(job().JobError):
                job().load_spec(self.spec(**changes))
        with self.assertRaises(ValueError):
            self.run_main(["backtest", "--spec", str(self.spec(parameters={"skip": 5, "colour": "red"})),
                           "--local-dir", str(self.folder / "out")])

    def test_the_server_is_started_and_always_stopped(self):
        server, register = FakeServer(), FakeRegister()
        code, _, _ = self.run_main(["backtest", "--spec", str(self.spec()), "--local-dir", str(self.folder / "a")],
                                   server=server, register=register)
        self.assertEqual((code, server.actions), (0, ["start", "stop"]))
        self.assertEqual(register.calls[0], ("started", "backtest"))
        self.assertIn(("finished", "passed"), register.calls)

        failing, register = FakeServer(), FakeRegister()
        with self.assertRaises(FileNotFoundError):
            self.run_main(["backtest", "--spec", str(self.folder / "missing.json"), "--local-dir", str(self.folder)],
                          server=failing, register=register)
        self.assertEqual(failing.actions, ["start", "stop"])
        self.assertIn(("finished", "failed"), register.calls)

        kept = FakeServer()
        self.run_main(["backtest", "--spec", str(self.spec(report_name="kept")), "--local-dir", str(self.folder / "a"),
                       "--keep-server-running"], server=kept, register=FakeRegister())
        self.assertEqual((kept.actions, kept.current), (["start"], "Ready"))

    def test_another_run_keeps_the_server_on(self):
        server, register = FakeServer(), FakeRegister(others=1)
        _, result, _ = self.run_main(["backtest", "--spec", str(self.spec()), "--local-dir", str(self.folder / "b")],
                                     server=server, register=register)
        self.assertEqual(server.actions, ["start"])
        self.assertIn(("running", result["run_id"], job().MAX_RUN_SECONDS), register.calls)

        idle = FakeServer("Ready")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            job().main(["stop-server", "--if-idle"], context=job().Context(environ={}, server=idle,
                                                                          register=FakeRegister()))
        self.assertEqual(idle.actions, ["stop"])
        busy = FakeServer("Ready")
        with contextlib.redirect_stdout(out):
            job().main(["stop-server", "--if-idle"], context=job().Context(environ={}, server=busy,
                                                                          register=FakeRegister(others=2)))
        self.assertEqual(busy.actions, [])
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1])["left_on_for_other_runs"], 2)

    def test_export_in_the_daily_window_never_reads_the_live_server(self):
        window = lambda: datetime(2026, 11, 9, 8, 30, tzinfo=UTC)  # noqa: E731
        with self.assertRaises(job().DailyWindowError):
            self.run_main(["export-features", "--as-known-on", "2026-10-17", "--subject", ACCOUNT, "--local-dir",
                           str(self.folder)], silver=worked_example(), bronze=bronze(), now=window)
        # The replayed datasets read only storage, so they may run then.
        code, result, _ = self.run_main(["export-features", "--as-known-on", "2026-10-17", "--subject", ACCOUNT,
                                         "--dataset", "market_prices", "--local-dir", str(self.folder)],
                                        bronze=bronze(), now=window)
        self.assertEqual((code, result["datasets"]["market_prices"]["rows"]), (0, 2))
        self.assertEqual(result["parquet_written"], HAVE_PYARROW)
        self.assertTrue(job().in_daily_window(datetime(2026, 11, 9, 7, 50, tzinfo=UTC)))
        self.assertTrue(job().in_daily_window(datetime(2026, 11, 9, 9, 25, tzinfo=UTC)))
        self.assertFalse(job().in_daily_window(datetime(2026, 11, 9, 7, 49, 59, tzinfo=UTC)))
        self.assertFalse(job().in_daily_window(datetime(2026, 11, 9, 9, 26, tzinfo=UTC)))

    def test_export_through_the_job_records_each_dataset(self):
        register = FakeRegister()
        code, result, _ = self.run_main(["export-features", "--as-known-on", "2026-10-17", "--subject", ACCOUNT,
                                         "--local-dir", str(self.folder)], silver=worked_example(), bronze=bronze(),
                                        register=register)
        self.assertEqual(code, 0)
        self.assertEqual(result["datasets"]["nav_snapshots"]["rows"], 1)
        self.assertIn(("export", sorted(feature_store.DATASETS)), register.calls)
        self.assertTrue((self.folder / "features" / "manifests" / "2026-10-17" / f"{ACCOUNT}.json").is_file())

    def test_logins_are_checked_by_name(self):
        with self.assertRaises(job().JobError):
            job().PostgresSilverReader("postgresql://ibkr_stg_app:x@h/ibkr_staging", "t")
        with self.assertRaises(job().JobError):
            job().DatabaseRegister("postgresql://researchadmin:x@h/research", "t")
        job().PostgresSilverReader("postgresql://research_reader:x@h/ibkr_staging", "t")


class ServerControlTests(unittest.TestCase):
    RESOURCE = ("/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-ibkr-database-v11/providers/"
                "Microsoft.DBforPostgreSQL/flexibleServers/ibkrv2stg-research-pg")

    def control(self, states, *, fail_post=False):
        calls = []
        pending = list(states)

        def request(method, url, token):
            calls.append((method, url.split("flexibleServers/ibkrv2stg-research-pg")[1]))
            if method == "POST":
                return (403, b'{"error": "denied"}') if fail_post else (202, b"")
            state = pending.pop(0) if len(pending) > 1 else pending[0]
            return 200, json.dumps({"properties": {"state": state}}).encode()

        clock = iter(range(0, 100000, 15))
        control = job().ServerControl(self.RESOURCE, token=lambda: "token", request=request, sleep=lambda s: None,
                                      clock=lambda: next(clock))
        return control, calls

    def test_start_waits_until_ready(self):
        control, calls = self.control(["Stopped", "Starting", "Starting", "Ready"])
        self.assertEqual(control.start(), "Ready")
        self.assertEqual([c for c in calls if c[0] == "POST"], [("POST", "/start?api-version=2024-08-01")])
        self.assertEqual(control.actions, ["start"])

    def test_stop_waits_for_a_settling_server(self):
        control, calls = self.control(["Updating", "Ready", "Stopping", "Stopped"])
        self.assertEqual(control.stop(), "Stopped")
        self.assertEqual(control.actions, ["stop"])
        already, calls = self.control(["Stopped"])
        self.assertEqual(already.stop(), "Stopped")
        self.assertEqual([c[0] for c in calls], ["GET"])

    def test_refusals_and_bad_ids(self):
        control, _ = self.control(["Ready"], fail_post=True)
        with self.assertRaises(job().JobError):
            control.stop()
        for resource in ("", self.RESOURCE.replace("flexibleServers", "servers"),
                         "/subscriptions/x/resourceGroups/rg/providers/Microsoft.App/jobs/ibkrv2stg-web"):
            with self.subTest(resource), self.assertRaises(job().JobError):
                job().ServerControl(resource)
        stuck, _ = self.control(["Starting"])
        with self.assertRaises(job().JobError):
            stuck.start()


class SignalTests(unittest.TestCase):
    VIEW = PointInTimeView({
        "UP": {"2026-01-05": 100.0, "2026-01-06": 102.0, "2026-01-07": 104.0, "2026-01-08": 106.0},
        "DOWN": {"2026-01-05": 100.0, "2026-01-06": 99.0, "2026-01-07": 98.0, "2026-01-08": 97.0},
        "FLAT": {"2026-01-05": 100.0, "2026-01-06": 100.0, "2026-01-07": 100.0, "2026-01-08": 100.0},
        "JUMP": {"2026-01-05": 100.0, "2026-01-06": 100.0, "2026-01-07": 100.0, "2026-01-08": 200.0},
    })

    def test_known_answers(self):
        day = "2026-01-08"
        self.assertEqual(signals.momentum(lookback=3, skip=0, top=1)(self.VIEW, day), {"JUMP": 1.0})
        # Skipping the last session ignores JUMP's one-day spike.
        self.assertEqual(signals.momentum(lookback=2, skip=1, top=1)(self.VIEW, day), {"UP": 1.0})
        self.assertEqual(signals.momentum(lookback=3, skip=0, top=2, gross=0.5)(self.VIEW, day),
                         {"JUMP": 0.25, "UP": 0.25})
        self.assertEqual(signals.low_volatility(lookback=3, top=1)(self.VIEW, day), {"FLAT": 1.0})
        self.assertEqual(signals.mean_reversion(lookback=3, top=1)(self.VIEW, day), {"DOWN": 1.0})
        # Default: the better half; too little history: no position.
        self.assertEqual(signals.mean_reversion(lookback=3)(self.VIEW, day), {"DOWN": 0.5, "FLAT": 0.5})
        self.assertEqual(signals.momentum(lookback=10, skip=0)(self.VIEW, day), {})

    def test_settings_are_checked(self):
        self.assertEqual(signals.parameters("momentum"),
                         {"lookback": 126, "skip": 21, "top": None, "gross": 1.0, "positive_only": False})
        for name, settings in (("momentum", {"window": 3}), ("astrology", {}), ("momentum", {"lookback": 0}),
                               ("low_volatility", {"lookback": 1}), ("mean_reversion", {"gross": 2.0})):
            with self.subTest(name=name, settings=settings), self.assertRaises(ValueError):
                signals.build(name, settings, assets=["UP"])


# --- the roles ---------------------------------------------------------------------------------

FORBIDDEN_SCHEMAS = re.compile(r"\b(gold|bronze|ops|agent)\.", re.IGNORECASE)
FORBIDDEN_PUBLIC = re.compile(r"\bpublic\.(?!app_tenant_id\(\)|account_belongs_to_current_tenant\(text\))\w+")


class RoleScriptTests(unittest.TestCase):
    def test_reader_grants_name_silver_and_the_tenant_functions_only(self):
        reader = script("live_reader_role")
        sql = reader.dry_run_sql()
        self.assertIsNone(FORBIDDEN_SCHEMAS.search(sql), FORBIDDEN_SCHEMAS.search(sql))
        self.assertIsNone(FORBIDDEN_PUBLIC.search(sql))
        for word in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALL PRIVILEGES", "BYPASSRLS ", "SUPERUSER ", "CREATE ON",
                     "WITH GRANT OPTION"):
            self.assertNotIn(" " + word, sql.replace("NOBYPASSRLS", "").replace("NOSUPERUSER", ""), word)
        grants = [statement for statement, _ in reader.grant_statements()]
        selects = [s for s in grants if s.startswith("GRANT SELECT")]
        self.assertTrue(all(re.fullmatch(r"GRANT SELECT ON TABLE silver\.[a-z_]+ TO research_reader", s) for s in selects))
        self.assertEqual(len(selects), 21 + 6 + 5)
        # The two live views read bronze live_* tables with the caller's rights, so they are left out.
        self.assertFalse({"subject_live_positions", "subject_live_summary"} & set(reader.SILVER_VIEWS))
        self.assertEqual(sorted(s for s in grants if s.startswith("GRANT EXECUTE")), sorted(
            [f"GRANT EXECUTE ON FUNCTION {f} TO research_reader" for f in reader.ROW_SECURITY_FUNCTIONS]
            + [f"GRANT EXECUTE ON FUNCTION silver.{t}_as_known(timestamptz) TO research_reader"
               for t in feature_store.HISTORY_TABLES]))
        self.assertIn("NOBYPASSRLS", reader.ROLE_ATTRIBUTES)
        self.assertIn("SET default_transaction_read_only = on", reader.ROLE_SETTINGS)
        catalog = json.loads((ROOT / "infra" / "medallion" / "catalog.json").read_text(encoding="utf-8"))["postgres"]
        for name in reader.SILVER_TABLES + reader.SILVER_VIEWS:
            entry = catalog.get(f"public.{name}") or catalog.get(f"silver.{name}")
            self.assertIsNotNone(entry, name)
            self.assertEqual(entry["layer"], "silver", name)

    def test_only_the_named_roles_are_accepted(self):
        reader, server = script("live_reader_role"), script("research_server_roles")
        for role in ("postgres", "research_reader; DROP ROLE x", "ibkr_stg_app"):
            with self.subTest(role), self.assertRaises(ValueError):
                reader.grant_statements(role)
            with self.subTest(role), self.assertRaises(ValueError):
                server.schema_statements(job=role)
        reader.grant_statements("research_reader_test_0123abcd")

    def test_the_least_privilege_check_catches_extra_rights(self):
        reader = script("live_reader_role")
        clean = {"attributes": {"superuser": False, "bypass_row_security": False, "create_role": False,
                                "create_database": False, "replication": False, "login": True, "inherit": False,
                                "connection_limit": 3},
                 "member_of": [], "settings": ["default_transaction_read_only=on"],
                 "database": {"connect": True, "create": False, "temporary": True},
                 "schemas": {"silver": {"usage": True, "create": False}, "public": {"usage": True, "create": False}},
                 "readable": ["silver.accounts", "silver.trades_history"], "writable": [], "sequences": [],
                 "functions": [], "definer_functions": []}
        self.assertEqual(reader.least_privilege_problems(clean), [])
        for change in ({"readable": ["silver.accounts", "gold.certified_figures"]}, {"writable": ["silver.trades"]},
                       {"member_of": ["ibkr_stg_app"]}, {"settings": []},
                       {"schemas": {**clean["schemas"], "bronze": {"usage": True, "create": False}}},
                       {"definer_functions": ["public.rotate_secrets()"]},
                       {"attributes": {**clean["attributes"], "bypass_row_security": True}}):
            with self.subTest(change):
                self.assertTrue(reader.least_privilege_problems({**clean, **change}))

    def test_the_job_login_writes_research_only(self):
        server = script("research_server_roles")
        sql = server.dry_run_sql()
        self.assertIsNone(FORBIDDEN_SCHEMAS.search(sql))
        grants = [s for s in server.schema_statements() if s.startswith("GRANT")]
        self.assertEqual(grants, ["GRANT CONNECT ON DATABASE research TO research_job",
                                  "GRANT USAGE ON SCHEMA research TO research_job",
                                  "GRANT SELECT, INSERT, UPDATE ON TABLE research.job_runs TO research_job",
                                  "GRANT SELECT, INSERT ON TABLE research.feature_exports TO research_job"])
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertIn("NOBYPASSRLS", server.JOB_ATTRIBUTES)


# --- the Bicep ---------------------------------------------------------------------------------

def _blocks(text: str, kind: str) -> dict[str, str]:
    """Symbol -> body of each resource of a type (Bicep text, braces counted)."""
    found = {}
    for match in re.finditer(r"^resource (\w+) '" + re.escape(kind) + r"@[\w-]+'( existing)? = (?:if \([^)]*\) )?(\[for [^:]+: )?\{",
                             text, re.MULTILINE):
        depth, index = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[index], 0)
            index += 1
        found[match.group(1)] = ("existing " if match.group(2) else "") + text[match.end():index]
    return found


class BicepTests(unittest.TestCase):
    TEXT = BICEP.read_text(encoding="utf-8")
    CONTAINERS = "Microsoft.Storage/storageAccounts/blobServices/containers"

    def test_roles_go_to_the_research_identity_only_and_storage_roles_to_single_containers(self):
        assignments = _blocks(self.TEXT, "Microsoft.Authorization/roleAssignments")
        self.assertEqual(len(assignments), 10)
        containers = _blocks(self.TEXT, self.CONTAINERS)
        reader_role, contributor_role = "storageBlobDataReaderRoleId", "storageBlobDataContributorRoleId"
        scopes = {}
        for name, body in assignments.items():
            with self.subTest(name):
                self.assertIn("principalId: researchIdentity.properties.principalId", body)
                self.assertEqual(body.count("principalId:"), 1)
                scope = re.search(r"scope: (\w+)", body)
                self.assertIsNotNone(scope, "every assignment names its scope; none is on the resource group")
                scopes[name] = (scope.group(1), re.search(r"roleDefinitionId: ([^\n]+)", body).group(1))
        storage = {name: scope for name, (scope, role) in scopes.items() if role in (reader_role, contributor_role)}
        self.assertTrue(set(storage.values()) <= set(containers), storage)
        reads = {scope for scope, role in scopes.values() if role == reader_role}
        writes = {scope for scope, role in scopes.values() if role == contributor_role}
        names = {symbol: re.search(r"name: '([^']+)'", body).group(1) for symbol, body in containers.items()}
        self.assertEqual({names[s] for s in reads}, {"ibkr-raw", "bronze-market-data", "bronze-vendor-data"})
        self.assertEqual({names[s] for s in writes}, {"research-features", "research-experiments"})
        others = {scope for scope, role in scopes.values() if role not in (reader_role, contributor_role)}
        self.assertEqual(others, {"registry", "researchDatabaseUrlSecret", "liveReaderUrlSecret", "vendorSecrets",
                                  "researchServer"})

    def test_no_existing_identity_is_granted_anything(self):
        identities = _blocks(self.TEXT, "Microsoft.ManagedIdentity/userAssignedIdentities")
        self.assertEqual(list(identities), ["researchIdentity"])
        self.assertFalse(identities["researchIdentity"].startswith("existing"))
        for name in ("web-identity", "ingestion-identity", "publisher-identity", "ibkrv2stg-agent", "approved-actions",
                     "diagnostics-identity", "specialist", "identityResourceId"):
            self.assertNotIn(name, self.TEXT, name)
        self.assertNotIn("principalId: '", self.TEXT)  # no literal principal ids

    def test_the_storage_account_is_only_referenced(self):
        self.assertTrue(_blocks(self.TEXT, "Microsoft.Storage/storageAccounts")["storage"].startswith("existing"))
        self.assertTrue(_blocks(self.TEXT, "Microsoft.Storage/storageAccounts/blobServices")["blobService"]
                        .startswith("existing"))
        created = {re.search(r"name: '([^']+)'", body).group(1)
                   for body in _blocks(self.TEXT, self.CONTAINERS).values() if not body.startswith("existing")}
        self.assertEqual(created, {"bronze-vendor-data", "research-features", "research-experiments"})
        for word in ("immutab", "legalHold", "managementPolicies", "allowSharedKeyAccess"):
            self.assertNotIn(word, self.TEXT.replace("No immutability", ""), word)

    def test_tags_server_and_job_settings(self):
        self.assertIn("layer: 'research'", self.TEXT)
        self.assertIn("hop: 'research'", self.TEXT)
        server = _blocks(self.TEXT, "Microsoft.DBforPostgreSQL/flexibleServers")["researchServer"]
        for setting in ("tier: 'Burstable'", "geoRedundantBackup: 'Disabled'", "mode: 'Disabled'",
                        "publicNetworkAccess: 'Disabled'", "passwordAuth: 'Enabled'"):
            self.assertIn(setting, server)
        self.assertIn("param postgresSkuName string = 'Standard_B1ms'", self.TEXT)
        self.assertRegex(self.TEXT, r"@secure\(\)\n@description\('From Key Vault only[^\n]*\n"
                                    r"param administratorLoginPassword string\n")
        self.assertIn("param scheduleHourUtc int = -1", self.TEXT)  # manual by default
        self.assertIn("triggerType: scheduled ? 'Schedule' : 'Manual'", self.TEXT)
        self.assertIn("scheduleMinuteOfDay >= 470 && scheduleMinuteOfDay <= 565", self.TEXT)  # 07:50 to 09:25
        self.assertGreater(float(re.search(r"param cpu string = '([\d.]+)'", self.TEXT).group(1)), 1.0)
        self.assertGreater(int(re.search(r"param memory string = '(\d+)Gi'", self.TEXT).group(1)), 2)
        role = _blocks(self.TEXT, "Microsoft.Authorization/roleDefinitions")["serverStartStopRole"]
        self.assertEqual(sorted(re.findall(r"'(Microsoft\.DBforPostgreSQL/[^']+)'", role)),
                         ["Microsoft.DBforPostgreSQL/flexibleServers/read",
                          "Microsoft.DBforPostgreSQL/flexibleServers/start/action",
                          "Microsoft.DBforPostgreSQL/flexibleServers/stop/action"])
        jobs = _blocks(self.TEXT, "Microsoft.App/jobs")
        self.assertEqual(set(jobs), {"researchJob", "idleStopJob"})
        for body in jobs.values():
            self.assertIn("'${researchIdentity.id}': {}", body)
            self.assertNotIn("identityResourceId", body)
        self.assertIn("param jobName string = 'ibkrv2stg-research'", self.TEXT)
        for secret in ("research-database-url", "research-live-reader-url"):
            self.assertIn(f"secretRef: '{secret}'", self.TEXT)

    def test_bicep_builds(self):
        az = shutil.which("az")
        if az is None or subprocess.run([az, "bicep", "version"], capture_output=True).returncode:
            self.skipTest("az bicep is not available here; CI builds every infra/*.bicep file")
        run = subprocess.run([az, "bicep", "build", "--file", str(BICEP), "--stdout"], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)


# --- the image and its helpers ------------------------------------------------------------------

class ImageTests(unittest.TestCase):
    def test_the_lock_copies_requirements_lock_and_adds_scipy_and_pyarrow(self):
        lock = script("lock_research_requirements")
        platform = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        research = (ROOT / "requirements-research.lock").read_text(encoding="utf-8")
        code, result = lock.check(platform, research)
        self.assertTrue(result["in_step_with_requirements_lock"], result)
        self.assertIn(code, (0, 2))
        self.assertLessEqual(set(result["without_hashes"]), {"scipy", "pyarrow"})
        copied = {(e["name"], e["line"]): e["hashes"] for e in lock.entries(platform)}
        for entry in lock.entries(research):
            if entry["name"] in lock.ADDED:
                continue
            self.assertEqual(sorted(copied[(entry["name"], entry["line"])]), sorted(entry["hashes"]), entry["name"])
        self.assertEqual({e["name"] for e in lock.entries(research)}, set(lock.FROM_PLATFORM) | set(lock.ADDED))
        self.assertFalse({"fastapi", "openai", "pandas", "yfinance", "reportlab"} & set(result["packages"]))
        drifted = platform.replace("psycopg==3.3.6 \\", "psycopg==3.3.7 \\")
        self.assertEqual(lock.check(drifted, research)[0], 1)

    def test_the_platform_images_stay_as_they_are(self):
        platform = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        self.assertNotIn("scipy==", platform)
        self.assertNotIn("pyarrow==", platform)
        for name in ("Dockerfile", "Dockerfile.azure", "Dockerfile.risk", "Dockerfile.agent"):
            path = ROOT / name
            if path.exists():
                self.assertNotIn("research", path.read_text(encoding="utf-8"), name)

    def test_dockerfile(self):
        text = (ROOT / "Dockerfile.research").read_text(encoding="utf-8")
        base = re.search(r"^FROM (\S+)", (ROOT / "Dockerfile.risk").read_text(encoding="utf-8"), re.MULTILINE).group(1)
        self.assertIn(f"FROM {base}\n", text)
        self.assertIn("--require-hashes --only-binary=:all: -r requirements-research.lock", text)
        self.assertNotIn("requirements.lock", text.replace("requirements-research.lock", ""))
        self.assertIn("USER 10001:10001\nRUN python scripts/research/check_research_image.py\n", text)
        self.assertNotIn("COPY . ", text)

    def test_the_build_context_holds_source_only(self):
        release = script("release_research_image")
        names = release.source_names()
        self.assertIn("Dockerfile.research", names)
        self.assertIn("scripts/research/run_research_job.py", names)
        self.assertFalse([n for n in names if not (n.startswith(("src/ibkr_etl/", "scripts/research/"))
                                                   or n in ("Dockerfile.research", "requirements-research.lock"))])
        self.assertFalse([n for n in names if n.startswith("scripts/research/") and not n.endswith(".py")])
        with tempfile.TemporaryDirectory() as folder:
            result = release.write_context(work=Path(folder))
            context = Path(result["context"])
            shipped = {p.relative_to(context).as_posix() for p in context.rglob("*") if p.is_file()}
            self.assertEqual(shipped, set(names) | {"SOURCE_MANIFEST.json"})
            manifest = json.loads((context / "SOURCE_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["tree_sha256"], result["source_tree_sha256"])
            self.assertFalse(manifest["deployment_authorized"])
        docker = release.build_commands("/ctx", "abc1234", acr=False)
        self.assertEqual(docker[0][-1], "/ctx")
        self.assertIn("ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-research:main-abc1234", docker[0])
        self.assertEqual(release.build_commands("/ctx", "abc1234", acr=True)[0][:3], ["az", "acr", "build"])
        with tempfile.TemporaryDirectory() as folder:
            lock = Path(folder) / "requirements-research.lock"
            lock.write_text("# header\nnumpy==2.5.3 \\\n    --hash=sha256:" + "0" * 64 + "\nscipy==1.16.1\n"
                            "    # hashes missing: run scripts/research/lock_research_requirements.py --fill\n")
            self.assertFalse(release.lock_ready(Path(folder)))
            lock.write_text("# header\nnumpy==2.5.3 \\\n    --hash=sha256:" + "0" * 64 + "\nscipy==1.16.1 \\\n"
                            "    --hash=sha256:" + "1" * 64 + "\n")
            self.assertTrue(release.lock_ready(Path(folder)))
        lines =release.record_lines("sha256:" + "a" * 64, "abc1234")
        self.assertEqual(set(lines["deploy/deployed-images.json images"]),
                         {"ibkrv2stg-research", "ibkrv2stg-research-idle-stop"})

    def test_switch_on_dry_runs_print_no_secret(self):
        switch = script("switch_on")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(switch.main(["secrets"]), 0)
            self.assertEqual(switch.main(["deploy"]), 0)
            self.assertEqual(switch.main(["deploy", "--image-digest", "sha256:" + "b" * 64, "--code-version", "abc1234"]), 0)
            self.assertEqual(switch.main(["deploy", "--image-digest", "latest"]), 1)
        text = out.getvalue()
        self.assertIn('"stage": "foundation"', text)
        self.assertIn('"stage": "jobs"', text)
        self.assertIsNone(re.search(r"postgresql://[^:\s]+:(?!<password>)[^@\s]+@", text))
        self.assertTrue(switch.in_daily_window(datetime(2026, 11, 9, 8, 0, tzinfo=UTC)))
        self.assertEqual(len(switch._password()), 43)


if __name__ == "__main__":
    unittest.main()
