"""``POST /openai/v1/images/generations`` and ``/images/edits`` — text-to-image, reference images, edits."""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, Request

from gemini_webapi_proxy.auth import require_api_key_if_configured
from gemini_webapi_proxy.config import get_settings
from gemini_webapi_proxy.schemas import ImageGenerationRequest, ImageGenerationResponse
from gemini_webapi_proxy.services.chat import create_image_generation
from gemini_webapi_proxy.utils.media import normalize_image_field

router = APIRouter(
    prefix="/openai/v1",
    tags=["images"],
    dependencies=[Depends(require_api_key_if_configured)],
)


@router.post("/images/generations", response_model=ImageGenerationResponse)
async def image_generations(body: ImageGenerationRequest):
    settings = get_settings()
    refs = normalize_image_field(body.image)
    if not refs and body.extra_body:
        refs = normalize_image_field(body.extra_body.get("image"))
    try:
        return await create_image_generation(
            settings,
            model=body.model,
            prompt=body.prompt,
            n=body.n,
            size=body.size,
            response_format=body.response_format,
            reference_images=refs or None,
        )
    except TimeoutError:
        from gemini_webapi_proxy.routes._errors import error_json

        return error_json("Image generation timed out", 504)
    except Exception as exc:
        from gemini_webapi_proxy.routes._errors import map_api_error

        return await map_api_error(exc, settings)


@router.post("/images/edits", response_model=ImageGenerationResponse)
async def image_edits(request: Request):
    """OpenAI ``images.edit``: multipart ``image`` / ``image[]`` files + ``prompt``.

    A JSON body with ``image`` as data/http URLs is accepted too.  ``mask`` is ignored:
    Gemini edits by instruction, not by mask.
    """
    settings = get_settings()
    ctype = request.headers.get("content-type") or ""
    if ctype.startswith(("multipart/", "application/x-www-form-urlencoded")):
        form = await request.form()
        refs = []
        for key in ("image", "image[]"):
            for item in form.getlist(key):
                if isinstance(item, str):
                    refs.extend(normalize_image_field(item))
                    continue
                mime = item.content_type or "image/png"
                data = base64.b64encode(await item.read()).decode()
                refs.append(f"data:{mime};base64,{data}")
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
    else:
        fields = await request.json()
        refs = normalize_image_field(fields.get("image"))
    prompt = str(fields.get("prompt") or "").strip()
    if not refs or not prompt:
        from gemini_webapi_proxy.routes._errors import error_json

        return error_json("images/edits needs at least one image and a prompt", 400)
    try:
        return await create_image_generation(
            settings,
            model=str(fields.get("model") or "gemini-2.5-flash-image"),
            prompt=prompt,
            n=int(fields.get("n") or 1),
            size=fields.get("size") or None,
            response_format="b64_json",
            reference_images=refs,
            edit=True,
        )
    except TimeoutError:
        from gemini_webapi_proxy.routes._errors import error_json

        return error_json("Image edit timed out", 504)
    except Exception as exc:
        from gemini_webapi_proxy.routes._errors import map_api_error

        return await map_api_error(exc, settings)
