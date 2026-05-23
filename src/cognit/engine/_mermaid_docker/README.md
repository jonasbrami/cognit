# Dockerised mermaid validator

Tiny parse-only mermaid validator. Used by `engine/mermaid.py` when `mmdc` is not
installed locally but `docker` is.

**Why this exists.** The official `@mermaid-js/mermaid-cli` Docker image bundles
Puppeteer + Chromium (~500MB) because `mmdc` does image rendering. We only need
*parsing* to validate LLM-generated diagrams, so this image installs just the
`mermaid` JS library + `jsdom` (~200MB total).

**Built lazily.** `cognit` runs `docker build` the first time it needs the
validator and an image isn't already present. After that, validation runs in
milliseconds per diagram via `docker run`.

**Build manually:**

```bash
docker build -t cognit-mermaid-validator:local src/cognit/engine/_mermaid_docker/
```

**Use manually:**

```bash
echo 'flowchart LR
A --> B' | docker run --rm -i cognit-mermaid-validator:local
```

Exit code 0 = valid, 1 = parse error (message on stderr).
