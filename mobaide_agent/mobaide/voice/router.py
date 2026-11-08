"""``/api/voice`` routes (direct transport).

The relay dispatcher (``relay/client.py`` ``_dispatch_voice``) answers the
same paths with the same service calls, models and error bodies.
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from mobaide.agent.auth.dependencies import get_current_user

from .errors import VoiceError, http_error_for
from .models import (
    VoiceCleanupRequest,
    VoiceCleanupResponse,
    VoiceCleanupSettingsRequest,
    VoiceDictationAudioRequest,
    VoiceDictationAudioResponse,
    VoiceDictationCreateRequest,
    VoiceDictationFinishRequest,
    VoiceDictationResponse,
    VoiceDictationResultResponse,
    VoiceInstallRequest,
    VoiceStatusResponse,
)
from .service import VoiceService

router = APIRouter(
    prefix="/api/voice",
    tags=["voice"],
    dependencies=[Depends(get_current_user)],
)


def _service(request: Request) -> VoiceService:
    service = getattr(request.app.state, "voice", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "voice_unavailable", "message": "Voice input is not available."},
        )
    return service


def _raise_mapped(exc: VoiceError) -> NoReturn:
    mapped = http_error_for(exc)
    assert mapped is not None
    raise HTTPException(status_code=mapped[0], detail=mapped[1]["detail"])


@router.get("/status", response_model=VoiceStatusResponse)
async def voice_status(request: Request) -> VoiceStatusResponse:
    return _service(request).status()


@router.post("/install", response_model=VoiceStatusResponse)
async def voice_install(
    request: Request, body: VoiceInstallRequest | None = None
) -> VoiceStatusResponse:
    try:
        return await _service(request).install(body.model_id if body else None)
    except VoiceError as exc:
        _raise_mapped(exc)


@router.post("/install/cancel", response_model=VoiceStatusResponse)
async def voice_install_cancel(request: Request) -> VoiceStatusResponse:
    try:
        return await _service(request).cancel_install()
    except VoiceError as exc:
        _raise_mapped(exc)


@router.delete("/model", response_model=VoiceStatusResponse)
async def voice_remove(request: Request) -> VoiceStatusResponse:
    try:
        return await _service(request).remove()
    except VoiceError as exc:
        _raise_mapped(exc)


@router.post("/dictations", response_model=VoiceDictationResponse)
async def voice_dictation_create(
    request: Request, body: VoiceDictationCreateRequest | None = None
) -> VoiceDictationResponse:
    del body
    try:
        return await _service(request).create_dictation()
    except VoiceError as exc:
        _raise_mapped(exc)


@router.post("/dictations/{dictation_id}/audio", response_model=VoiceDictationAudioResponse)
async def voice_dictation_audio(
    dictation_id: str, body: VoiceDictationAudioRequest, request: Request
) -> VoiceDictationAudioResponse:
    try:
        return await _service(request).append_audio(dictation_id, body)
    except VoiceError as exc:
        _raise_mapped(exc)


@router.post("/dictations/{dictation_id}/finish", response_model=VoiceDictationResultResponse)
async def voice_dictation_finish(
    dictation_id: str, body: VoiceDictationFinishRequest, request: Request
) -> VoiceDictationResultResponse:
    try:
        return await _service(request).finish_dictation(dictation_id, body)
    except VoiceError as exc:
        _raise_mapped(exc)


@router.delete("/dictations/{dictation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def voice_dictation_delete(dictation_id: str, request: Request) -> Response:
    await _service(request).delete_dictation(dictation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/cleanup", response_model=VoiceCleanupResponse)
async def voice_cleanup(body: VoiceCleanupRequest, request: Request) -> VoiceCleanupResponse:
    try:
        return await _service(request).cleanup_transcript(body.text, body.kind)
    except VoiceError as exc:
        _raise_mapped(exc)


@router.put("/cleanup/settings", response_model=VoiceStatusResponse)
async def voice_cleanup_settings(
    body: VoiceCleanupSettingsRequest, request: Request
) -> VoiceStatusResponse:
    try:
        return _service(request).set_cleanup_enabled(body.enabled)
    except VoiceError as exc:
        _raise_mapped(exc)
