# Tiny parse-only mermaid validator. No Chromium, no rendering — just mermaid.parse().
#
# The official `@mermaid-js/mermaid-cli` image (~500MB) bundles Puppeteer +
# Chromium for image rendering. We only need parsing, so we install just the
# `mermaid` library + a Node-side DOM shim (jsdom). Image size: ~200MB.
FROM node:20-alpine

WORKDIR /app

# Manual package.json so npm doesn't complain. ESM type so mermaid v11 works.
# Pinned to specific minor versions for reproducible builds — bump deliberately
# when picking up a new mermaid release rather than letting a silent upstream
# change drift the validator.
RUN echo '{"name":"cognit-mermaid-validator","version":"1.0.0","type":"module","private":true}' > package.json \
 && npm install --no-audit --no-fund --no-progress mermaid@11.15.0 jsdom@24.0.0 \
 && npm cache clean --force

COPY validate.mjs /app/validate.mjs

ENTRYPOINT ["node", "/app/validate.mjs"]
