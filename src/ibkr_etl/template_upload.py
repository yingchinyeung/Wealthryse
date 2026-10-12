from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from xml.etree import ElementTree

from pypdf import PdfReader


MAX_TEMPLATE_BYTES = 50 * 1024 * 1024
MAX_PRESENTATION_EXPANDED_BYTES = 250 * 1024 * 1024
MAX_PRESENTATION_ARCHIVE_ENTRIES = 20_000
SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")
PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
STRATEGY_NAME_PATTERN = re.compile(r"^iStrategy\s*:\s*\S(?:.{0,139}\S)?$", re.I)
ALPHA_WORLD_SHARED_TEMPLATE_SUBJECTS = frozenset(
    {"alpha-world-ms", "alpha-world-t"}
)
ALPHA_WORLD_FAMILY_IDENTITY = "alphaworld"


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    content: bytes
    content_type: str
    sha256: str

    @property
    def byte_count(self) -> int:
        return len(self.content)


def validate_subject_id(value: str) -> str:
    subject_id = value.strip()
    if not SUBJECT_PATTERN.fullmatch(subject_id):
        raise RuntimeError("The onboarding account identifier is invalid")
    return subject_id


def _bounded_content(filename: str, content: bytes) -> bytes:
    if not filename.strip():
        raise RuntimeError("Every required onboarding document must have a filename")
    if not content:
        raise RuntimeError(f"The uploaded document is empty: {filename}")
    if len(content) > MAX_TEMPLATE_BYTES:
        raise RuntimeError(f"The uploaded document exceeds 50 MiB: {filename}")
    return content


def validate_pdf_upload(filename: str, content: bytes) -> ValidatedUpload:
    payload = _bounded_content(filename, content)
    if PurePosixPath(filename.replace("\\", "/")).suffix.lower() != ".pdf":
        raise RuntimeError("Factsheet documents must use the .pdf extension")
    if not payload.startswith(b"%PDF-"):
        raise RuntimeError("A factsheet upload is not a valid PDF file")
    try:
        reader = PdfReader(io.BytesIO(payload), strict=True)
        if len(reader.pages) < 1:
            raise RuntimeError("A factsheet PDF must contain at least one page")
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError("A factsheet upload is malformed or unreadable") from error
    return ValidatedUpload(
        filename=PurePosixPath(filename.replace("\\", "/")).name,
        content=payload,
        content_type="application/pdf",
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def validate_pptx_upload(filename: str, content: bytes) -> ValidatedUpload:
    payload = _bounded_content(filename, content)
    if PurePosixPath(filename.replace("\\", "/")).suffix.lower() != ".pptx":
        raise RuntimeError("Presentation templates must use the .pptx extension")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_PRESENTATION_ARCHIVE_ENTRIES:
                raise RuntimeError("The presentation contains too many archive entries")
            # ZipInfo.filename can normalize Windows separators or truncate a
            # NUL. Validate the original member name before any such aliasing.
            names = [info.orig_filename for info in entries]
            if len({name.casefold() for name in names}) != len(names):
                raise RuntimeError("The presentation contains duplicate archive paths")
            if any(
                not name or name.startswith("/") or "\\" in name or ":" in name
                or "\x00" in name or any(part in {".", "..", ""} for part in name.rstrip("/").split("/"))
                for name in names
            ):
                raise RuntimeError("The presentation contains an unsafe archive path")
            if any(info.flag_bits & 1 for info in entries):
                raise RuntimeError("Encrypted presentation archives are not accepted")
            if any(info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED} for info in entries):
                raise RuntimeError("The presentation uses unsupported compression; save it again as a standard PowerPoint .pptx file")
            required = {"[Content_Types].xml", "ppt/presentation.xml"}
            if not required.issubset(names) or not any(
                re.fullmatch(r"ppt/slides/slide\d+\.xml", name) for name in names
            ):
                raise RuntimeError("The presentation upload is not a valid PPTX deck")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise RuntimeError("Macro-enabled presentation files are not accepted")
            if sum(info.file_size for info in entries) > MAX_PRESENTATION_EXPANDED_BYTES:
                raise RuntimeError("The presentation expands beyond the safety limit")
            if archive.testzip() is not None:
                raise RuntimeError("The presentation contains corrupt archive contents")
    except RuntimeError:
        raise
    except (zipfile.BadZipFile, OSError, EOFError, ValueError, NotImplementedError, zlib.error) as error:
        raise RuntimeError("The presentation upload is malformed or unreadable") from error
    return ValidatedUpload(
        filename=PurePosixPath(filename.replace("\\", "/")).name,
        content=payload,
        content_type=PPTX_CONTENT_TYPE,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def normalize_strategy_identity(value: str) -> str:
    """Return a stable comparison key despite PowerPoint run splitting."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def validate_strategy_name(value: str) -> str:
    strategy_name = " ".join(str(value or "").split())
    if not STRATEGY_NAME_PATTERN.fullmatch(strategy_name):
        raise RuntimeError(
            "The presentation strategy title must use the format "
            "'iStrategy: Strategy Name' and be no longer than 160 characters"
        )
    if len(normalize_strategy_identity(strategy_name)) < len("istrategy") + 4:
        raise RuntimeError("The presentation strategy title is too short")
    return strategy_name


def strategy_name_from_display_name(subject_id: str, display_name: str) -> str:
    """Derive the client-facing strategy title from the approved subject name.

    The document uploader is intentionally not allowed to provide this value.
    A subject such as ``U16088606-Greater China`` therefore has exactly one
    permitted presentation identity: ``iStrategy: Greater China``.
    """

    subject = validate_subject_id(subject_id)
    display = " ".join(str(display_name or "").split())
    if not display:
        raise RuntimeError("The approved subject display name is missing")
    descriptor = re.sub(
        rf"^{re.escape(subject)}\s*",
        "",
        display,
        count=1,
        flags=re.I,
    ).strip(" \t-–—:()[]")
    if not descriptor:
        raise RuntimeError(
            "The approved subject display name must include a strategy description"
        )
    if descriptor.lower().startswith("istrategy"):
        return validate_strategy_name(descriptor)
    return validate_strategy_name(f"iStrategy: {descriptor}")


def presentation_identity_scope(
    *, subject_id: str, display_name: str, layout_policy: str
) -> dict[str, object]:
    """Create the deterministic scope that an owner approves before upload."""

    if layout_policy not in {"platform_standard", "subject_template"}:
        raise RuntimeError("The presentation layout policy is not supported")
    subject = validate_subject_id(subject_id)
    display = " ".join(str(display_name or "").split())
    strategy_name = strategy_name_from_display_name(subject, display)
    return {
        "schema_version": 1,
        "subject_id": subject,
        "source_display_name": display,
        "source_display_identity_sha256": hashlib.sha256(
            normalize_strategy_identity(display).encode("utf-8")
        ).hexdigest(),
        "strategy_name": strategy_name,
        "strategy_identity_sha256": hashlib.sha256(
            normalize_strategy_identity(strategy_name).encode("utf-8")
        ).hexdigest(),
        "layout_policy": layout_policy,
        "derivation": "approved_subject_display_name",
    }


def presentation_strategy_contract(
    content: bytes,
    *,
    expected_strategy: str,
    layout_policy: str,
    subject_id: str | None = None,
) -> dict[str, object]:
    """Bind an uploaded deck to one explicit strategy and layout policy.

    Every paragraph containing an ``iStrategy:`` marker must identify the
    expected strategy. This rejects a copied deck that retains another
    account's title even if the expected title was added somewhere else.
    """

    strategy_name = validate_strategy_name(expected_strategy)
    if layout_policy not in {"platform_standard", "subject_template"}:
        raise RuntimeError("The presentation layout policy is not supported")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            slide_names = sorted(
                (
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                ),
                key=lambda name: int(re.search(r"\d+", name).group()),
            )
            paragraphs: list[str] = []
            for slide_name in slide_names:
                root = ElementTree.fromstring(archive.read(slide_name))
                for paragraph in root.iter(f"{{{DRAWING_NS}}}p"):
                    text = "".join(
                        node.text or ""
                        for node in paragraph.iter(f"{{{DRAWING_NS}}}t")
                    ).strip()
                    if text:
                        paragraphs.append(text)
    except (zipfile.BadZipFile, ElementTree.ParseError, OSError) as error:
        raise RuntimeError(
            "The presentation strategy identity could not be inspected"
        ) from error

    expected_identity = normalize_strategy_identity(strategy_name)
    visible_identity = normalize_strategy_identity(" ".join(paragraphs))
    exact_identity_present = expected_identity in visible_identity
    exception_id: str | None = None
    if not exact_identity_present and subject_id in ALPHA_WORLD_SHARED_TEMPLATE_SUBJECTS:
        # Alpha World MS and Alpha World T intentionally share a family-level
        # presentation reference. The generated output remains bound to the
        # owner-approved subject identity; this exception only permits the
        # common source template at intake.
        if ALPHA_WORLD_FAMILY_IDENTITY in visible_identity:
            exception_id = "alpha_world_shared_family_template_v1"
    if not exact_identity_present and exception_id is None:
        raise RuntimeError(
            f"The presentation does not contain the required strategy title "
            f"{strategy_name!r}"
        )
    strategy_markers = [
        paragraph
        for paragraph in paragraphs
        if re.match(r"^\s*istrategy\s*:", paragraph, re.I)
    ]
    if not strategy_markers and exception_id is None:
        raise RuntimeError("The presentation contains no visible iStrategy title")
    allowed_family_identity = normalize_strategy_identity("iStrategy: Alpha World")
    mismatches = [
        marker
        for marker in strategy_markers
        if normalize_strategy_identity(marker) != "istrategy"
        and expected_identity not in normalize_strategy_identity(marker)
        and not (
            exception_id is not None
            and allowed_family_identity in normalize_strategy_identity(marker)
        )
    ]
    if mismatches:
        raise RuntimeError(
            "The presentation contains an iStrategy title from another account"
        )
    contract: dict[str, object] = {
        "schema_version": 1,
        "strategy_name": strategy_name,
        "strategy_identity_sha256": hashlib.sha256(
            expected_identity.encode("utf-8")
        ).hexdigest(),
        "visible_text_sha256": hashlib.sha256(
            "\n".join(paragraphs).encode("utf-8")
        ).hexdigest(),
        "strategy_marker_count": len(strategy_markers),
        "slide_count": len(slide_names),
        "layout_policy": layout_policy,
        "source_template_sha256": hashlib.sha256(content).hexdigest(),
        "identity_validation": (
            "shared_family_template_exception"
            if exception_id is not None
            else "exact_strategy_title"
        ),
    }
    if exception_id is not None:
        contract.update(
            {
                "identity_exception_id": exception_id,
                "identity_exception_subject_id": subject_id,
                "identity_exception_scope": sorted(
                    ALPHA_WORLD_SHARED_TEMPLATE_SUBJECTS
                ),
            }
        )
    return contract


def upload_version_label(now: datetime | None = None) -> str:
    timestamp = now or datetime.now(timezone.utc)
    return f"portal-upload-{timestamp.strftime('%Y%m%dT%H%M%SZ')}"


def safe_blob_filename(upload: ValidatedUpload, *, label: str) -> str:
    suffix = PurePosixPath(upload.filename).suffix.lower()
    return f"{label}-{upload.sha256[:16]}{suffix}"
