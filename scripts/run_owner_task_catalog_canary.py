"""Synthetic stdout-only transport rehearsal; no SQL, credentials or Azure SDK.

The trusted controller embeds this source with the exact codec/target modules.
No environment discovery, network, file writes or model calls occur here. The
bounded post-emission hold lets the controller observe the real running replica;
it is diagnostic timing, not identity or delivery proof.
"""
from dataclasses import asdict
import hashlib
import time

from ibkr_etl.owner_codex_catalog_transport import Binding, MAX_RECORD_BYTES, canonical, encode_record


HOLD_SECONDS = 300


def synthetic_record(binding):
    fields = ("purpose", "profile_key", "job_resource_id", "operation_id", "source_sha256")
    if type(binding) is not Binding or any(type(getattr(binding, key)) is not str for key in fields):
        raise ValueError("catalog_canary_binding_required")
    binding = Binding(**{key: getattr(binding, key) for key in fields})
    if binding.purpose != "canary":
        raise ValueError("catalog_canary_binding_required")
    # The wire record really is 4 MiB; gzip only packages the small worker code.
    value = dict(kind="synthetic_catalog_transport_canary", binding=asdict(binding),
        credentials_supplied=False, sql_calls=0, source_rows_read=0,
        source_rows_written=0, agents_activated=0, synthetic_payload="")
    needed = MAX_RECORD_BYTES - len(canonical(value))
    if needed <= 0:
        raise ValueError("catalog_canary_record_size_invalid")
    value["synthetic_payload"] = "".join(hashlib.sha256(str(n).encode("ascii")).hexdigest()
        for n in range((needed + 63) // 64))[:needed]
    if len(canonical(value)) != MAX_RECORD_BYTES:
        raise ValueError("catalog_canary_record_size_invalid")
    return value


def main(binding_values):
    binding = Binding(**binding_values)
    lines = encode_record(synthetic_record(binding), binding)
    # Flush each complete protocol line. Never print exceptions or raw inputs.
    for line in lines:
        print(line, flush=True)
    time.sleep(HOLD_SECONDS)

