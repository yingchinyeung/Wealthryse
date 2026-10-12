"""Read-only historical cache audit; no bootstrap, fetching or report writes.

For each active subject, compare first/middle/latest NAV dates against current
consumed inputs. Inventory covers all NAV dates; value checking is only sampled.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
import psycopg
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl import risk_snapshots as risk


def representative_dates(dates):
    dates = sorted(set(dates))
    return sorted({dates[0], dates[len(dates)//2], dates[-1]}) if dates else []


def emit(kind, **fields):
    print("RISK_CACHE_AUDIT " + json.dumps({"kind":kind, **fields}, sort_keys=True), flush=True)


def changed_result_sections(stored, recalculated):
    """Compare outputs without logging any underlying portfolio values."""
    from ibkr_etl.util import canonical_json
    def stable(value):
        if isinstance(value, dict):
            return {key:stable(item) for key,item in value.items() if key != "generated_at"}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    return [key for key in ("metrics", "expected_tail_loss_contributors", "crisis_replay", "risk_free", "quality_status")
            if canonical_json(stable(stored.get(key))) != canonical_json(stable(recalculated.get(key)))]


def main():
    # The launcher injects reviewed source for this diagnostic only, without
    # mutating installed image files or the persistent production job command.
    if "SOURCE_RISK" in globals():
        exec(compile(SOURCE_RISK, "reviewed-risk-snapshots", "exec"), risk.__dict__)
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
                         options="-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=5000",
                         row_factory=_hybrid_row_factory) as raw:
        connection = PostgresConnection(raw)
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', ?, true)", (tenant,))
        captured = connection.execute("SELECT now() AS captured, current_setting('transaction_read_only') AS read_only").fetchone()
        if captured["read_only"] != "on":
            raise RuntimeError("Audit must be database-enforced read-only")
        subjects = connection.execute("SELECT subject_id FROM platform_subjects WHERE tenant_id=? AND lifecycle_status='active' ORDER BY subject_id", (tenant,)).fetchall()
        states = Counter()
        sample_count = 0
        recalculations = 0
        for subject in subjects:
            subject_id = subject["subject_id"]
            dates = [str(r["snapshot_date"]) for r in connection.execute(
                "SELECT DISTINCT snapshot_date FROM subject_nav_series WHERE tenant_id=? AND subject_id=? ORDER BY snapshot_date", (tenant, subject_id)).fetchall()]
            cached = {str(r["as_of_date"]):r["quality_status"] for r in connection.execute(
                "SELECT as_of_date, quality_status FROM risk_analytics_snapshots WHERE tenant_id=? AND subject_id=? AND analytics_version=?",
                (tenant, subject_id, risk.RISK_ANALYTICS_VERSION)).fetchall()}
            emit("inventory", subject_id=subject_id, nav_dates=len(dates),
                 covered_dates=sum(d in cached for d in dates), missing_dates=sum(d not in cached for d in dates),
                 cached_quality=dict(Counter(cached.values())), sampled_dates=representative_dates(dates))
            for day in representative_dates(dates):
                inputs, timings = risk.load_risk_inputs(connection, tenant_id=tenant, subject_id=subject_id, as_of=day)
                fingerprint = risk.risk_input_fingerprint(inputs)
                existing = connection.execute("""SELECT comparison_version, source_data_sha256,
                    payload_sha256, payload_json, quality_status FROM risk_analytics_snapshots
                    WHERE tenant_id=? AND subject_id=? AND as_of_date=? AND analytics_version=?""",
                    (tenant, subject_id, day, risk.RISK_ANALYTICS_VERSION)).fetchone()
                state = risk.cache_reuse_status(existing, inputs=inputs, source_sha256=fingerprint)
                output_check = {"status":"not_recalculated"}
                if globals().get("ALLOW_SAMPLE_RECALCULATION", False) and state != "current" and existing is not None:
                    new_payload, _, _ = risk.compute_risk_snapshot(connection, tenant_id=tenant,
                        subject_id=subject_id, as_of=day, loaded_inputs=(inputs, timings))
                    try:
                        old_payload = risk.decoded_payload(existing["payload_json"])
                        changed = changed_result_sections(old_payload, new_payload)
                        output_check = {"status":"different" if changed else "same_results", "changed_sections":changed}
                    except (ValueError, TypeError):
                        output_check = {"status":"stored_payload_unreadable"}
                    recalculations += 1
                states[state] += 1
                sample_count += 1
                emit("sample", subject_id=subject_id, as_of=day, status=state,
                     source_sha256=fingerprint, input_query_ms=timings["input_query_ms"],
                     comparison_rows=len(inputs["rows"]),
                     latest_comparison_date=inputs["rows"][-1].observation_date if inputs["rows"] else None,
                     risk_free_available=inputs["risk_free"] is not None,
                     crisis_available=inputs["crisis_replay"] is not None,
                     output_check=output_check)
        emit("complete", mode="read_only_sample", captured_at=str(captured["captured"]),
             subjects=len(subjects), samples=sample_count, states=dict(states),
             in_memory_recalculations=recalculations,
             calculation_writes=0, published_report_writes=0,
             limits=["Only first/middle/latest dates value-checked", "Consumed-input checks cannot certify upstream corrections"])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit("error", error_type=type(error).__name__)
        sys.exit(1)
