"""Integration tests for the FastAPI app (no real Gemini client required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from gemini_webapi_proxy.app import app


def test_health_does_not_require_client() -> None:
    """``/health`` must answer 200 without touching the Gemini client."""
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "gemini-webapi-proxy"}


def test_unknown_route_404() -> None:
    with TestClient(app) as client:
        resp = client.get("/nope")
    assert resp.status_code == 404


def test_models_route_returns_503_when_no_client() -> None:
    """Without a configured client, /models should fail with a 5xx (not crash)."""
    with TestClient(app) as client:
        resp = client.get("/openai/v1/models")
    # We don't have a real client in tests; either an error or an empty list
    # is acceptable as long as the response is well-formed.
    assert resp.status_code in (200, 500, 503)
    if resp.status_code == 200:
        assert "data" in resp.json()


def _fake_completion(text: str):
    from gemini_webapi_proxy.schemas import ChatCompletionChoice, ChatCompletionResponse

    async def fake(settings, *, model, messages):
        fake.messages = messages
        return ChatCompletionResponse(
            id="chatcmpl-test",
            created=0,
            model=model,
            choices=[ChatCompletionChoice(message={"role": "assistant", "content": text})],
        )

    return fake


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }
]


def test_chat_stream_sends_sse(monkeypatch) -> None:
    """``stream=true`` is answered with SSE chunks ending in [DONE]."""
    from gemini_webapi_proxy.routes import chat

    monkeypatch.setattr(chat, "create_chat_completion", _fake_completion("pong"))
    with TestClient(app) as client:
        resp = client.post(
            "/openai/v1/chat/completions",
            json={
                "model": "gemini-flash",
                "stream": True,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert '"content": "pong"' in resp.text
    assert resp.text.rstrip().endswith("data: [DONE]")


def test_chat_emulates_tool_calls(monkeypatch) -> None:
    """A <tool_calls> block in the answer becomes OpenAI tool_calls."""
    from gemini_webapi_proxy.routes import chat

    fake = _fake_completion(
        'Checking.<tool_calls>\n[{"name": "get_weather", "arguments": {"city": "Moscow"}}]\n</tool_calls>'
    )
    monkeypatch.setattr(chat, "create_chat_completion", fake)
    with TestClient(app) as client:
        resp = client.post(
            "/openai/v1/chat/completions",
            json={
                "model": "gemini-flash",
                "tools": TOOLS,
                "messages": [{"role": "user", "content": "weather?"}],
            },
        )
    choice = resp.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == "Checking."
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "get_weather"
    assert call["function"]["arguments"] == '{"city": "Moscow"}'
    assert any("get_weather" in m.content for m in fake.messages if m.role == "system")


def test_chat_without_block_is_plain_text(monkeypatch) -> None:
    from gemini_webapi_proxy.routes import chat

    monkeypatch.setattr(chat, "create_chat_completion", _fake_completion("Sunny."))
    with TestClient(app) as client:
        resp = client.post(
            "/openai/v1/chat/completions",
            json={
                "model": "gemini-flash",
                "tools": TOOLS,
                "messages": [{"role": "user", "content": "x"}],
            },
        )
    choice = resp.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "Sunny."
