# Website-only overlay: retain existing dependencies, templates and entrypoint.
FROM ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d0614cf32c524117bae1df447478b5f5fd85e253db45e742a9017d2db6084fe4
USER root
WORKDIR /app
# Do not silently bundle unrelated local source changes into the live website.
RUN python -c "import hashlib; from pathlib import Path; assert hashlib.sha256(Path('/app/src/ibkr_etl/web.py').read_bytes()).hexdigest() == '5b02f6d77b90d82af7294644c6cfeec65d93f7c62a7b08fceee85abfd20a1a35'"
COPY web.py /app/src/ibkr_etl/web.py
COPY tests/ /tmp/ui-cleanup-tests/
USER 10001:10001
RUN PYTHONPATH=/app/src:/app/scripts:/tmp/ui-cleanup-tests python -m unittest test_codex_workspace test_agent_daily_inbox.InboxWebTests test_cancelled_deployment_ui test_web
# No environment, package, font, report asset or command changes.
