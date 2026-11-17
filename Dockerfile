FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000 PYTHONPATH=/app BIDBOX_DB_PATH=/var/lib/bidbox/data/bidbox.duckdb
WORKDIR /app
COPY pyproject.toml app.html ./
COPY agent ./agent
COPY audit ./audit
COPY bidbox ./bidbox
COPY bidbox_mcp ./bidbox_mcp
COPY scripts ./scripts
COPY evaluation ./evaluation
COPY apps ./apps
COPY data/fixtures ./data/fixtures
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home bidbox && mkdir -p /var/lib/bidbox/data /var/lib/bidbox/reports /var/lib/bidbox/runs && chown -R bidbox:bidbox /var/lib/bidbox
USER bidbox
WORKDIR /var/lib/bidbox
VOLUME ["/var/lib/bidbox"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/health', timeout=4)"
CMD ["python", "-m", "bidbox.serve"]
