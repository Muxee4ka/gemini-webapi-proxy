"""Unit tests for video jobs (services/video.py, routes/videos.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from gemini_webapi_proxy.services import video


def test_classify_follows_gemini_web_behaviour() -> None:
    assert video.classify("", videos=1, images=0) == "completed"
    # Exhausted video limit: Gemini silently draws a picture instead.
    assert video.classify("Here you go", videos=0, images=1) == "limit"
    assert (
        video.classify("You've reached your limit, try again later", videos=0, images=0) == "limit"
    )
    assert (
        video.classify("I encountered an error, please try again", videos=0, images=0) == "glitch"
    )
    assert video.classify("I'm having a hard time fulfilling your request", videos=0, images=0) == (
        "refused"
    )


def test_video_prompt_mentions_reference_and_duration() -> None:
    text = video.video_prompt("a cat jumps", has_reference=True, seconds="8", size="1280x720")
    assert "animates the attached image" in text
    assert "8 seconds" in text
    assert "1280x720" in text
    assert video.video_prompt("x", has_reference=False, seconds=None, size=None).startswith(
        "Create a video (not an image)"
    )


class _FakeVideo:
    async def save(self, path: str, filename: str):
        out = Path(path) / f"{filename}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        return {"video": str(out), "video_thumbnail": None}


def _fake_client(output, seen: dict | None = None):
    client = SimpleNamespace(cookies={}, timeout=120, watchdog_timeout=120)

    async def generate_content(prompt, files=None, model=None, **kwargs):
        if seen is not None:
            seen.update(kwargs, timeout=client.timeout, watchdog_timeout=client.watchdog_timeout)
        return output

    client.generate_content = generate_content
    return client


def _settings(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path, video_timeout=5, video_keep_hours=24, video_cooldown=60, http_proxy=None
    )


def _run(monkeypatch, tmp_path, output, client=None) -> video.VideoJob:
    fake = client or _fake_client(output)
    seen: dict = {}

    async def run(settings, model, call, **kwargs):
        seen.update(kwargs)
        return await call(fake, model)

    monkeypatch.setattr(video.pool, "run", run)
    monkeypatch.setattr(video, "sanitize_gemini_client_cookies", lambda c: None)
    job = video.VideoJob(id="video_test", model="gemini-flash", prompt="a cat")
    asyncio.run(video.run_job(_settings(tmp_path), job))
    # Video limits are their own: a used-up video quota must not cool chat down.
    assert seen["cooldown_key"].startswith("video:")
    return job


def test_run_job_saves_video(monkeypatch, tmp_path) -> None:
    job = _run(monkeypatch, tmp_path, SimpleNamespace(text="", images=[], videos=[_FakeVideo()]))
    assert job.status == "completed"
    assert job.path and job.path.exists()


def test_video_call_waits_out_silence_without_resending(monkeypatch, tmp_path) -> None:
    seen: dict = {}
    client = _fake_client(SimpleNamespace(text="", images=[], videos=[_FakeVideo()]), seen)
    _run(monkeypatch, tmp_path, None, client)
    assert seen == {"current_retry": 0, "timeout": 5, "watchdog_timeout": 5}
    assert (client.timeout, client.watchdog_timeout) == (120, 120)


def test_run_job_image_instead_of_video_is_limit(monkeypatch, tmp_path) -> None:
    job = _run(monkeypatch, tmp_path, SimpleNamespace(text="Here", images=[object()], videos=[]))
    assert job.status == "failed"
    assert job.error["code"] == "video_limit_reached"


def test_routes_create_status_and_content(monkeypatch, tmp_path) -> None:
    from gemini_webapi_proxy.app import app
    from gemini_webapi_proxy.routes import videos

    created = {}

    def create_job(settings, **kwargs):
        job = video.VideoJob(id="video_x", **{k: kwargs[k] for k in ("model", "prompt")})
        job.references = kwargs["references"]
        video._jobs[job.id] = job
        created.update(kwargs)
        return job

    monkeypatch.setattr(videos.video, "create_job", create_job)
    with TestClient(app) as client:
        resp = client.post(
            "/openai/v1/videos",
            data={"prompt": "a cat", "seconds": "8"},
            files={"input_reference": ("ref.png", b"\x89PNG", "image/png")},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"
        assert created["references"][0].startswith("data:image/png;base64,")
        assert created["seconds"] == "8"
        assert client.get("/openai/v1/videos/video_x/content").status_code == 409
        job = video._jobs["video_x"]
        job.status, job.path = "completed", tmp_path / "v.mp4"
        job.path.write_bytes(b"mp4")
        content = client.get("/openai/v1/videos/video_x/content")
        assert content.status_code == 200
        assert content.headers["content-type"] == "video/mp4"
        assert client.get("/openai/v1/videos/nope").status_code == 404
    video._jobs.pop("video_x", None)
