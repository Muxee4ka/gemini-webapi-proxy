# Documentation Index

| Document | Description |
|----------|-------------|
| [../README.md](../README.md) | Project overview and quick start |
| [configuration.md](./configuration.md) | All environment variables |
| [cookie-sources.md](./cookie-sources.md) | How to provide Gemini Web cookies |
| [architecture.md](./architecture.md) | Deep dive: data flow, registry, pluggable seams |
| [downloaders.md](./downloaders.md) | Image download chain and how to extend it |
| [studio.md](./studio.md) | Wiring an OpenAI-compatible client to the proxy |
| [api.md](./api.md) | OpenAI-compatible API reference: chat (streaming, tool calling), images, edits, videos |
| [openclaw.md](./openclaw.md) | Using the proxy from OpenClaw: chat models, `image_generate`, video skill |
| [docker.md](./docker.md) | Docker, Compose, and bare-metal deployment |
| [troubleshooting.md](./troubleshooting.md) | Common errors and fixes |
| [../README.zh.md](../README.zh.md) | Chinese (中文) README |

## One-shot commands

```bash
# Install (editable, with browser-cookie + dev extras)
pip install -e ".[browser-cookie,dev]"

# Start the server on the default port
./start.sh

# Or via Docker
bash scripts/docker-up.sh

# End-to-end self-test (chat + image + b64 sanity check)
bash scripts/e2e-image.sh
```

## Repository layout (after refactor)

```
src/gemini_webapi_proxy/   importable package
  app.py                   FastAPI app factory
  config.py                pydantic-settings
  cookies/                 pluggable cookie sources
  client/                  Gemini client + registry + pool
  downloaders/             image download chain
  services/                chat / image / video / probe
  routes/                  FastAPI routers
  utils/                   small helpers
scripts/                   dev / ops shell scripts
integrations/openclaw/     OpenClaw skill for video generation
tests/                     pytest suites
docs/                      you are here
data/                      runtime data (gitignored)
```
