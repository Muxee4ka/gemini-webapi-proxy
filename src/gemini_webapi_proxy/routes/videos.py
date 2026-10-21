"""OpenAI Videos API shape: ``POST /videos`` → job, ``GET /videos/{id}`` → status,
``GET /videos/{id}/content`` → mp4.

Reference image: multipart ``input_reference`` file, or ``input_reference`` / ``image``
as a data/http URL in a JSON body.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from gemini_webapi_proxy.auth import require_api_key_if_configured
from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.routes._errors import error_json
from gemini_webapi_proxy.services import video
from gemini_webapi_proxy.stats import note
from gemini_webapi_proxy.utils.media import normalize_image_field

router = APIRouter(
    prefix="/openai/v1",
    tags=["videos"],
    dependencies=[Depends(require_api_key_if_configured)],
)

DEFAULT_MODEL = "gemini-flash"


async def _read_request(request: Request) -> tuple[dict, list[str]]:
    ctype = request.headers.get("content-type") or ""
    if ctype.startswith(("multipart/", "application/x-www-form-urlencoded")):
        form = await request.form()
        refs: list[str] = []
        for key in ("input_reference", "image", "image[]"):
            for item in form.getlist(key):
                if isinstance(item, str):
                    refs.extend(normalize_image_field(item))
                    continue
                mime = item.content_type or "image/png"
                refs.append(f"data:{mime};base64,{base64.b64encode(await item.read()).decode()}")
        return {k: v for k, v in form.items() if isinstance(v, str)}, refs
    body = await request.json()
    refs = normalize_image_field(body.get("input_reference")) or normalize_image_field(
        body.get("image")
    )
    return body, refs


@router.post("/videos")
async def create_video(request: Request):
    fields, refs = await _read_request(request)
    prompt = str(fields.get("prompt") or "").strip()
    if not prompt:
        return error_json("prompt is required", 400)
    job = video.create_job(
        get_settings(),
        model=str(fields.get("model") or DEFAULT_MODEL),
        prompt=prompt,
        seconds=str(fields["seconds"]) if fields.get("seconds") else None,
        size=fields.get("size") or None,
        references=refs,
    )
    note(model=job.model, video=job.id)
    return job.to_openai()


@router.get("/videos")
async def list_videos():
    return {"object": "list", "data": [j.to_openai() for j in video.list_jobs()]}


@router.get("/videos/{video_id}")
async def get_video(video_id: str):
    job = video.get_job(video_id)
    if not job:
        return error_json(f"video {video_id} not found", 404)
    return job.to_openai()


@router.get("/videos/{video_id}/content")
async def get_video_content(video_id: str):
    job = video.get_job(video_id)
    if not job:
        return error_json(f"video {video_id} not found", 404)
    if job.status != "completed" or not job.path or not job.path.exists():
        return error_json(f"video {video_id} is {job.status}", 409)
    return FileResponse(job.path, media_type="video/mp4", filename=f"{job.id}.mp4")
