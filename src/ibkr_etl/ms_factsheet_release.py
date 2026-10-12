"""Validate MS against its own approved layout and immutable numerical inputs."""
import io
from .monthly_artifacts import (
    MonthlyArtifactError, factsheet_inputs, validate_alpha_world_factsheet_layout,
)
from .alpha_world_ms_pdf_validation import validate as validate_pdf_accuracy


def validate_ms_factsheet_release(content: bytes, snapshot: dict, *, period_end: str) -> None:
    if (snapshot.get('subject_id') != 'alpha-world-ms'
            or snapshot.get('reporting_currency') != 'HKD'
            or snapshot.get('period_end') != period_end):
        raise MonthlyArtifactError('MS release input identity, currency or period differs')
    inputs = factsheet_inputs(snapshot)
    validate_alpha_world_factsheet_layout(io.BytesIO(content), inputs)
    validate_pdf_accuracy(io.BytesIO(content), snapshot)
