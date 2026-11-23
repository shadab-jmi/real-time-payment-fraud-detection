FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY requirements.lock .
RUN pip install --no-cache-dir --only-binary=:all: --no-deps -r requirements.lock && pip check
COPY ops/monitoring.lock /tmp/monitoring.lock
RUN pip install --only-binary=:all: --no-deps -r /tmp/monitoring.lock && cat /tmp/monitoring.lock >> requirements.lock && pip check
COPY packages/ packages/
COPY tests/ tests/
COPY simulator/ simulator/
COPY ml/ ml/
COPY apps/ apps/
RUN mkdir /credentials && chown 65534:65534 /credentials
RUN mkdir /artifacts && chown 65534:65534 /artifacts
COPY alembic.ini .
USER 65534:65534
ENTRYPOINT ["python", "-m", "packages.tools"]
