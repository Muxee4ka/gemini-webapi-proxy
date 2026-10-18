"""Unit tests for the tool-calling emulation in ``routes/chat.py``."""

from __future__ import annotations

import json

from gemini_webapi_proxy.routes.chat import _extract_calls, _match_name, _normalize

KNOWN = {"image_generate", "exec", "read"}


def test_match_name_exact_and_renamed() -> None:
    assert _match_name("exec", KNOWN) == "exec"
    # Gemini Web renames tools after its own built-ins.
    assert _match_name("image_gen", KNOWN) == "image_generate"
    assert _match_name("nonexistent", KNOWN) is None


def test_extract_calls_tagged_block() -> None:
    text = 'ok <tool_calls>[{"name": "read", "arguments": {"path": "/a"}}]</tool_calls>'
    before, calls = _extract_calls(text, KNOWN)
    assert before == "ok"
    assert calls[0]["function"] == {"name": "read", "arguments": '{"path": "/a"}'}
    assert calls[0]["id"].startswith("call_")


def test_extract_calls_fenced_and_markdown_escaped() -> None:
    text = '<tool_calls>\n```json\n[{"name": "image\\_gen", "arguments": {"prompt": "cat"}}]\n```\n</tool_calls>'
    _, calls = _extract_calls(text, KNOWN)
    assert calls[0]["function"]["name"] == "image_generate"


def test_extract_calls_unknown_tool_is_ignored() -> None:
    assert (
        _extract_calls('<tool_calls>[{"name": "rm_rf", "arguments": {}}]</tool_calls>', KNOWN)
        is None
    )
    assert _extract_calls("plain answer", KNOWN) is None


def test_normalize_renders_history_and_inserts_tools_block() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path": "/a"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "data"},
    ]
    out = _normalize(messages, "TOOLS")
    assert [m.role for m in out] == ["system", "system", "user", "assistant", "user"]
    assert out[1].content == "TOOLS"
    assert (
        json.loads(out[3].content.split("<tool_calls>\n")[1].split("\n</tool_calls>")[0])[0]["name"]
        == "read"
    )
    assert out[4].content.startswith('<tool_result name="read" id="c1">')
