# RoleGraph runs as a single container. No build step, no bundler, no sidecars.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ROLEGRAPH_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml README.md ./
COPY rolegraph ./rolegraph
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir .

COPY config ./config
COPY data/demo ./data/demo
COPY docs ./docs

# The application never needs to write inside the image; only /data is mutable.
RUN useradd --create-home --uid 10001 rolegraph \
 && mkdir -p /data \
 && chown -R rolegraph:rolegraph /data /app
USER rolegraph

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "rolegraph.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
