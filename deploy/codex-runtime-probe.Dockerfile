FROM python:3.12-slim-bookworm@sha256:9901e0a8d75037d8242ed43155cbcb2d1f61be1356383d8054afb59fd50e39c4
WORKDIR /probe
ENV PYTHONPATH=/probe/src PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY codex-runtime.lock.json /probe/codex-runtime.lock.json
COPY scripts/install_pinned_codex_runtime.py /probe/scripts/install_pinned_codex_runtime.py
RUN python scripts/install_pinned_codex_runtime.py && \
    groupadd --gid 10001 probe && \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin probe
COPY src /probe/src
COPY scripts/run_codex_runtime_probe.py /probe/scripts/run_codex_runtime_probe.py
USER 10001:10001
RUN python scripts/run_codex_runtime_probe.py --mode offline
CMD ["python", "scripts/run_codex_runtime_probe.py", "--mode", "offline"]
