# Communication candidate only. Retain the exact live runtime and private assets.
ARG VERIFIED_BASE_IMAGE
FROM ${VERIFIED_BASE_IMAGE}
USER root
WORKDIR /app
COPY requirements.lock /tmp/ai-inbox-requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/ai-inbox-requirements.lock
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY tests/ /app/tests/
COPY infra/ /app/infra/
COPY migrations/ /app/migrations/
COPY deploy/ /app/deploy/
COPY Dockerfile Dockerfile.azure Dockerfile.risk pyproject.toml /app/
ENV PYTHONPATH=/app/src
USER 10001:10001
RUN python -m unittest discover -s /app/tests
# Preserve the base image entrypoint, command, fonts, report assets and Node tools.
