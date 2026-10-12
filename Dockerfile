FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    IBKR_PROJECT_ROOT=/app

WORKDIR /app

RUN apt-get update && \
    apt-get install --yes --no-install-recommends ca-certificates nodejs npm && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 ibkr && \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin ibkr

COPY pyproject.toml ./
COPY requirements.lock ./
COPY src ./src
COPY migrations ./migrations
COPY scripts ./scripts
COPY queries ./queries
COPY ["output/pptx/U16088571_Monthly_Presentation_2026-08-31_Enhanced_v24.pptx", "/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx"]
COPY ["output/pdf/U16088571_Factsheet_2026-08-31_Approved_v6.pdf", "/app/templates/U16088571/approved-august-2026-factsheet-v6.pdf"]
COPY ["private-inputs/U16088571/monthly factsheet template.pdf", "/app/templates/U16088571/approved-august-2026-factsheet-template.pdf"]
COPY ["private-inputs/U16088606/monthly factsheet template.pdf", "/app/templates/U16088606/intake/monthly factsheet template.pdf"]
COPY ["private-inputs/U16088606/monthly presentation deck template.pptx", "/app/templates/U16088606/intake/monthly presentation deck template.pptx"]
COPY ["private-inputs/U16088606/previously completed factsheet.pdf", "/app/templates/U16088606/intake/previously completed factsheet.pdf"]
COPY ["private-inputs/U16088606/template-intake-manifest.json", "/app/templates/U16088606/intake/template-intake-manifest.json"]

# The PowerPoint renderer uses the same private Artifact Tool runtime that was
# used to approve the August layout. Replace its Windows canvas binary with the
# Linux build during the Azure image build.
RUN rm -rf /app/scripts/node_modules/@oai/artifact-tool/node_modules/skia-canvas && \
    npm install --prefix /app --no-save --ignore-scripts=false skia-canvas@3.0.8

RUN python -m pip install --require-hashes -r requirements.lock && \
    python -m pip install --no-deps . && \
    mkdir -p /app/data /app/raw /app/reports /app/logs && \
    chown -R ibkr:ibkr /app

USER 10001:10001

# Fail the build rather than discover missing report dependencies in production.
RUN python scripts/check_report_runtime.py

CMD ["python", "scripts/run_cloud_job.py"]
