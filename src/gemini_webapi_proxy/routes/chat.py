"""``POST /openai/v1/chat/completions`` — local compat layer for agent clients (OpenClaw).

Upstream route rejects ``stream=true`` and ``content: null`` and knows nothing about
``tools``; agent clients need all three. Here:

* ``stream=true`` — the full answer is generated first, then sent as SSE chunks;
* tool calling is emulated in the prompt: tool specs are injected as a system block,
  Gemini answers with ``<tool_calls>[...]</tool_calls>``, which is parsed back into
  OpenAI ``tool_calls`` (``finish_reason: "tool_calls"``);
* history ``tool_calls`` / ``tool`` messages are rendered in the same text format;
* upstream refusal is 502, not 403, so clients don't mark the provider as auth-broken.

``COMPAT_DUMP_DIR`` (env) — when set, each request/answer pair is written there (debug).
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gemini_webapi_proxy.auth import require_api_key_if_configured
from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.schemas import ChatMessage
from gemini_webapi_proxy.services.chat import create_chat_completion

log = logging.getLogger("gemini_webapi_proxy.compat")

router = APIRouter(
    prefix="/openai/v1",
    tags=["chat"],
    dependencies=[Depends(require_api_key_if_configured)],
)

DESC_LIMIT = 600

TOOLS_PREAMBLE = """# Tools

You can call the tools listed below. To call tools, reply with ONLY this block and nothing after it:

<tool_calls>
[{"name": "<tool name>", "arguments": {<arguments as a JSON object matching the schema>}}]
</tool_calls>

Rules:
- The content of the block must be valid JSON: a list of one or more calls. Do not wrap it in a code fence.
- Use the exact tool names from the list (not your own built-in tools) and only the parameters from their schemas.
- When a listed tool can do the task, call it instead of answering from your own knowledge or your built-in
  search, weather, or image generation: the client only sees results that come through its tools.
- You may write one short sentence before the block. Never invent tool results: after the block, stop and wait.
- Tool results come back in later messages as <tool_result name="..." id="...">...</tool_result>.
- If no tool is needed, answer normally without the block.
{choice}
Available tools:
{specs}
"""


def _text_of(content: Any) -> str | list[dict[str, Any]]:
    if content is None:
        return ""
    if isinstance(content, (str, list)):
        return content
    return json.dumps(content, ensure_ascii=False)


def _as_text(content: Any) -> str:
    content = _text_of(content)
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if (p.get("type") or "") == "text")


def _tool_specs(tools: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for tool in tools:
        fn = tool.get("function") or tool
        name = fn.get("name")
        if not name:
            continue
        desc = " ".join((fn.get("description") or "").split())
        if len(desc) > DESC_LIMIT:
            desc = desc[:DESC_LIMIT] + "…"
        params = json.dumps(
            fn.get("parameters") or {"type": "object"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines.append(f"## {name}\n{desc}\nparameters: {params}")
    return "\n\n".join(lines)


def _choice_rule(choice: Any) -> str:
    if choice == "required":
        return "- You MUST call at least one tool in this reply.\n"
    if isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name")
        if name:
            return f"- You MUST call the tool `{name}` in this reply.\n"
    return ""


def _render_calls(calls: list[dict[str, Any]]) -> str:
    items = []
    for c in calls:
        fn = c.get("function") or {}
        args: Any = fn.get("arguments") or "{}"
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            pass
        items.append({"name": fn.get("name"), "arguments": args})
    return "<tool_calls>\n" + json.dumps(items, ensure_ascii=False) + "\n</tool_calls>"


def _normalize(messages: list[dict[str, Any]], tools_block: str | None) -> list[ChatMessage]:
    out: list[ChatMessage] = []
    names_by_id: dict[str, str] = {}
    for msg in messages:
        role = str(msg.get("role") or "user")
        if role == "developer":
            role = "system"
        if role == "tool":
            call_id = msg.get("tool_call_id") or ""
            name = msg.get("name") or names_by_id.get(call_id) or "tool"
            out.append(
                ChatMessage(
                    role="user",
                    content=f'<tool_result name="{name}" id="{call_id}">\n'
                    f"{_as_text(msg.get('content'))}\n</tool_result>",
                )
            )
            continue
        content = _text_of(msg.get("content"))
        calls = msg.get("tool_calls") or []
        if calls:
            for c in calls:
                names_by_id[c.get("id") or ""] = (c.get("function") or {}).get("name") or "tool"
            content = f"{_as_text(content)}\n{_render_calls(calls)}".strip()
        out.append(ChatMessage(role=role, content=content))
    if tools_block:
        # After the client's own system prompt, so it is read last among the rules.
        pos = 0
        while pos < len(out) and out[pos].role == "system":
            pos += 1
        out.insert(pos, ChatMessage(role="system", content=tools_block))
    return out


_BLOCK_RE = re.compile(r"<tool_calls>\s*(.*?)\s*(?:</tool_calls>|$)", re.S)
_FENCE_RE = re.compile(r"```(?:json|tool_calls)?\s*(.*?)```", re.S)


def _loads_lenient(raw: str) -> Any:
    raw = raw.strip()
    fence = _FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()
    for candidate in (raw, re.sub(r"\\([_*\[\]#>~`|-])", r"\1", raw)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def _match_name(name: Any, known: set[str]) -> str | None:
    """Gemini Web sometimes renames tools after its own built-ins (``image_gen``)."""
    if not isinstance(name, str) or not name:
        return None
    if name in known:
        return name
    lowered = name.lower()
    near = [k for k in known if k.lower().startswith(lowered) or lowered.startswith(k.lower())]
    if len(near) == 1:
        return near[0]
    close = difflib.get_close_matches(name, list(known), n=2, cutoff=0.8)
    return close[0] if len(close) == 1 else None


def _extract_calls(text: str, known: set[str]) -> tuple[str, list[dict[str, Any]]] | None:
    m = _BLOCK_RE.search(text)
    if m:
        payload = _loads_lenient(m.group(1))
        before = text[: m.start()].strip()
    else:
        # Fallback: a bare fenced JSON call without the tags.
        fence = _FENCE_RE.search(text)
        if not fence:
            return None
        payload = _loads_lenient(fence.group(1))
        before = text[: fence.start()].strip()
    if isinstance(payload, dict):
        payload = payload.get("tool_calls") or [payload]
    if not isinstance(payload, list):
        return None
    calls: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = _match_name(item.get("name") or (item.get("function") or {}).get("name"), known)
        args = item.get("arguments", item.get("parameters", {}))
        if not name:
            continue
        if not isinstance(args, str):
            args = json.dumps(args or {}, ensure_ascii=False)
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": name, "arguments": args},
            }
        )
    return (before, calls) if calls else None


def _dump(body: dict[str, Any], answer: Any) -> None:
    root = os.environ.get("COMPAT_DUMP_DIR")
    if not root:
        return
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:6]}.json"
    (path / name).write_text(
        json.dumps({"request": body, "answer": answer}, ensure_ascii=False, indent=1)
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    settings = get_settings()
    model = body.get("model") or ""
    tools = [t for t in body.get("tools") or [] if isinstance(t, dict)]
    choice = body.get("tool_choice")
    use_tools = bool(tools) and choice != "none"
    known = {(t.get("function") or t).get("name") for t in tools}
    tools_block = (
        TOOLS_PREAMBLE.replace("{choice}", _choice_rule(choice)).replace(
            "{specs}", _tool_specs(tools)
        )
        if use_tools
        else None
    )
    messages = _normalize(body.get("messages") or [], tools_block)
    try:
        resp = await create_chat_completion(settings, model=model, messages=messages)
    except TimeoutError:
        from gemini_webapi_proxy.routes._errors import error_json

        return error_json("Request timed out", 504)
    except Exception as exc:
        from gemini_webapi_proxy.routes._errors import map_api_error

        err = await map_api_error(exc, settings)
        if err.status_code == 403:
            err.status_code = 502
        return err

    message = resp.choices[0].message
    text = message.get("content") or ""
    parsed = _extract_calls(text, known) if use_tools else None
    finish = "stop"
    if parsed:
        before, calls = parsed
        message = {"role": "assistant", "content": before or None, "tool_calls": calls}
        finish = "tool_calls"
        resp.choices[0].message = message
        resp.choices[0].finish_reason = finish
    log.info(
        "compat chat model=%s tools=%d prompt_chars=%d answer_chars=%d tool_calls=%s",
        model,
        len(tools),
        sum(len(_as_text(m.content)) for m in messages),
        len(text),
        [c["function"]["name"] for c in message.get("tool_calls") or []],
    )
    _dump(body, text)

    if not body.get("stream"):
        return JSONResponse(resp.model_dump())

    base = {
        "id": resp.id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": resp.model,
    }

    def chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
        return _sse(
            {
                **base,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
        )

    async def events():
        content = message.get("content")
        yield chunk({"role": "assistant", "content": content or ""})
        for i, call in enumerate(message.get("tool_calls") or []):
            yield chunk({"tool_calls": [{"index": i, **call}]})
        yield chunk({}, finish)
        if (body.get("stream_options") or {}).get("include_usage"):
            yield _sse({**base, "choices": [], "usage": resp.usage})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Request-Id": uuid.uuid4().hex[:12]},
    )
