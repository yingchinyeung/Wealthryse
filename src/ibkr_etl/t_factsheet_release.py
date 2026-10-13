"""Validate T against its own owner template and immutable numerical inputs."""
import hashlib
import io
from pathlib import Path

from .monthly_artifacts import MonthlyArtifactError
from .alpha_world_t_factsheet import AUTHORITY_SHA256
from .alpha_world_t_pdf_validation import validate as validate_pdf_accuracy

OWNER_TEMPLATE = Path(__file__).resolve().parents[2] / 'templates' / 'alpha-world-t' / 'owner_template_20261008.pdf'


def validate_t_factsheet_release(content: bytes, snapshot: dict, *, period_end: str) -> None:
    if (snapshot.get('subject_id') != 'alpha-world-t'
            or snapshot.get('reporting_currency') != 'HKD'
            or snapshot.get('period_end') != period_end):
        raise MonthlyArtifactError('T release input identity, currency or period differs')
    try:
        template = OWNER_TEMPLATE.read_bytes()
    except OSError as error:
        raise MonthlyArtifactError('T owner template is unavailable') from error
    if hashlib.sha256(template).hexdigest() != AUTHORITY_SHA256:
        raise MonthlyArtifactError('T owner template is not the approved authority')
    validate_pdf_accuracy(io.BytesIO(content), snapshot, template_path=io.BytesIO(template))
