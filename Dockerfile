# Single-stage image on python-slim + Playwright's own Chromium. `playwright install
# --with-deps chromium` pulls the browser build that MATCHES the installed playwright
# version and the OS libraries it needs, on both amd64 and arm64 — so the same
# Dockerfile works on an x86 box or an ARM (Pi/Proxmox-arm) server. See README §ARM64.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium \
 && python -m patchright install chromium \
 && apt-get update && apt-get install -y --no-install-recommends xvfb xauth \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m appuser \
 && mkdir -p /app/data \
 && chown -R appuser /app /ms-playwright

COPY app ./app
COPY config.yaml ./config.yaml
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

USER appuser
EXPOSE 8080

# Container-level health = the same /health the API serves.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').getcode()==200 else 1)"

# entrypoint.sh starts Xvfb (virtual display) then execs uvicorn, so Chromium runs
# HEADFUL (real browser) and passes Imperva's bot check where headless is blocked.
CMD ["/app/entrypoint.sh"]
