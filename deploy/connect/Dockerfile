FROM python:3.13-slim@sha256:3dd7cc108ec1493442514f5c2a871af6af0ec31d768ff6e378a93340c3b3db5f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-connect.txt requirements-relay.txt ./
RUN pip install --no-cache-dir -r requirements-relay.txt \
    && groupadd -g 10001 connect && useradd -u 10001 -g connect -M connect
COPY connect ./connect
COPY bridge ./bridge
COPY web ./web
USER 10001:10001
# Select relay or eligibility entry point explicitly in the compose service.
CMD ["python", "-m", "connect.relay", "--help"]
