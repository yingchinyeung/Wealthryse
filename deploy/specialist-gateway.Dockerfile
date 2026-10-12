FROM ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c
USER root
COPY gateway-requirements.lock /app/gateway-requirements.lock
RUN python -m pip install --no-deps --require-hashes -r /app/gateway-requirements.lock
COPY src/ibkr_etl /app/src/ibkr_etl
COPY scripts/serve_specialist_gateway.py /app/scripts/serve_specialist_gateway.py
COPY gateway-build.json /app/gateway-build.json
COPY tests /app/gateway-tests
ENV PYTHONPATH=/app/src
USER 10001:10001
RUN python -m unittest discover -s /app/gateway-tests -p 'test_agent_gateway*py'
CMD ["python", "scripts/serve_specialist_gateway.py"]
