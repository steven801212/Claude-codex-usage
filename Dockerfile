# --- stage 1: install the claude / codex CLIs (only used for interactive login) ---
FROM node:22-bookworm-slim AS cli
ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest
RUN npm install -g --prefix /opt/cli \
      @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
      @openai/codex@${CODEX_VERSION} \
 && npm cache clean --force

# --- stage 2: the panel ---
FROM python:3.12-slim-bookworm
COPY --from=cli /usr/local/bin/node /usr/local/bin/node
COPY --from=cli /opt/cli /opt/cli

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Everything the CLIs write lands under /data (the volume)
ENV PATH="/opt/cli/bin:${PATH}" \
    DATA_DIR=/data \
    HOME=/data/home \
    CLAUDE_CONFIG_DIR=/data/claude \
    CODEX_HOME=/data/codex \
    DISABLE_AUTOUPDATER=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Taipei

VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz',timeout=4).status==200 else 1)"
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787", "--proxy-headers", "--forwarded-allow-ips", "*"]
