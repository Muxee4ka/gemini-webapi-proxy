# Using the proxy from OpenClaw

[OpenClaw](https://openclaw.ai) can use the proxy for three things: Gemini as a chat
model, image generation and editing, and video through a workspace skill. The examples
assume the proxy is reachable at `https://gemini-api.example.com/openai/v1` with API key
`$GEMINI_PROXY_KEY`.

## Chat models

A custom OpenAI-compatible provider. Keep the provider id unique: an id that matches
a built-in provider or its alias (`google`, `gemini`, …) is merged into the built-in one.

```json5
{
  models: {
    providers: {
      "gemini-proxy": {
        baseUrl: "https://gemini-api.example.com/openai/v1",
        apiKey: "${GEMINI_PROXY_KEY}",
        api: "openai-completions",
        // Agent prompts with tool specs are long; Gemini Web answers slowly.
        timeoutSeconds: 300,
        models: [
          { id: "gemini-flash", name: "Gemini Flash (web)", input: ["text", "image"], contextWindow: 1000000, maxTokens: 8192 },
          { id: "gemini-argon", name: "Gemini Argon (web)", reasoning: true, input: ["text", "image"], contextWindow: 1000000, maxTokens: 8192 },
          { id: "gemini-pro", name: "Gemini Pro (web)", reasoning: true, input: ["text", "image"], contextWindow: 1000000, maxTokens: 8192 },
          { id: "gemini-flash-lite", name: "Gemini Flash-Lite (web)", input: ["text", "image"], contextWindow: 1000000, maxTokens: 8192 },
        ],
      },
    },
  },
  agents: {
    defaults: {
      modelPolicy: { allow: ["gemini-proxy/*" /* , … */] },
    },
  },
}
```

Tools work through the proxy's tool-calling emulation (see [api.md](api.md#tool-calling-emulated)):
an agent on `gemini-proxy/gemini-argon` can read files, run commands and call
`image_generate`.

If OpenClaw also stores the key in an auth profile (`openclaw models auth list` shows
`gemini-proxy:default`), update it when the key changes, or requests fail with 403 and
OpenClaw puts the whole provider on an "auth" cooldown:

```bash
printf "%s\n" "$GEMINI_PROXY_KEY" | openclaw models auth paste-api-key --provider gemini-proxy --profile-id gemini-proxy:default
```

## Images: `image_generate`

OpenClaw's image tool only works with built-in image providers, and a custom provider
cannot be one. The built-in **LiteLLM** provider speaks the OpenAI Images API
(`/images/generations`, `/images/edits`) at a configurable `baseUrl`, so point it at the
proxy. Nothing LiteLLM is involved; it is only the provider plugin's name.

```json5
{
  models: {
    providers: {
      litellm: {
        baseUrl: "https://gemini-api.example.com/openai/v1",
        apiKey: "${GEMINI_PROXY_KEY}",
        api: "openai-completions",
        models: [],
      },
    },
  },
  agents: {
    defaults: {
      mediaModels: {
        image: {
          primary: "litellm/gemini-2.5-pro-image",
          fallbacks: ["litellm/gemini-2.5-flash-image"],
          timeoutMs: 240000,
        },
      },
    },
  },
}
```

Like the chat provider, the `litellm:default` auth profile has to hold the same key.
Don't repoint the `openai` provider instead: an explicit `models.providers.openai`
switches it off ChatGPT/Codex OAuth.

## Video: the `gemini-video` skill

OpenClaw's `video_generate` has no provider with a configurable OpenAI-compatible
endpoint, so video goes through a workspace skill:
[`integrations/openclaw/gemini-video`](../integrations/openclaw/gemini-video).

1. Copy the folder to `<workspace>/skills/gemini-video/`.
2. Give the skill its env in `openclaw.json`:

   ```json5
   {
     skills: {
       entries: {
         "gemini-video": {
           enabled: true,
           env: {
             GEMINI_PROXY_URL: "https://gemini-api.example.com/openai/v1",
             GEMINI_PROXY_KEY: "<key>",
           },
         },
       },
     },
   }
   ```

3. Check: `openclaw skills list` shows `gemini-video` as `ready`.

The agent runs `scripts/gen_video.py` (stdlib only): it starts a `/videos` job, polls it,
saves the mp4 to `<workspace>/video-output/` and replies with a standalone
`MEDIA:/path.mp4` line, which OpenClaw delivers as an attachment. The skill tells the
agent never to retry on its own and to run one video at a time.

The script sends a browser-like `User-Agent`: Cloudflare in front of a proxy blocks the
default `Python-urllib` signature with `403, error 1010`.
