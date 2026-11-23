"""``/api/voice/cloud`` routes, for the signed-in user."""
from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from ..auth.dependencies import get_non_admin_api_user
from ..auth.models import UserTokenPayload
from .errors import CloudVoiceError
from .models import (
    CloudVoiceAudioRequest,
    CloudVoiceAudioResponse,
    CloudVoiceDictationResponse,
    CloudVoiceEngineRequest,
    CloudVoiceFinishRequest,
    CloudVoiceResultResponse,
    CloudVoiceStatus,
)
from .service import LIMITS, CloudVoiceService

router = APIRouter(prefix="/api/voice/cloud", tags=["cloud-voice"])


def _service(request: Request) -> CloudVoiceService:
    return request.app.state.cloud_voice_service


def _raise_mapped(exc: CloudVoiceError) -> NoReturn:
    raise HTTPException(status_code=exc.status, detail=exc.detail())


@router.get("", response_model=CloudVoiceStatus)
async def cloud_voice_status(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> CloudVoiceStatus:
    """Whether cloud voice input is offered to the caller and works now, its
    limits, today's use, and the caller's per-agent choices."""
    return CloudVoiceStatus(**await _service(request).status(user))


@router.put("/engines/{agent_id}", response_model=CloudVoiceStatus)
async def cloud_voice_set_engine(
    agent_id: str,
    body: CloudVoiceEngineRequest,
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> CloudVoiceStatus:
    """Choose where one agent's dictations are transcribed and push
    ``cloud_voice.changed`` to the caller's other devices."""
    try:
        return CloudVoiceStatus(
            **await _service(request).set_engine(user, agent_id, body.engine)
        )
    except CloudVoiceError as exc:
        _raise_mapped(exc)


@router.post("/dictations", response_model=CloudVoiceDictationResponse)
async def cloud_voice_dictation_create(
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> CloudVoiceDictationResponse:
    try:
        dictation_id = await _service(request).create(user)
    except CloudVoiceError as exc:
        _raise_mapped(exc)
    return CloudVoiceDictationResponse(dictation_id=dictation_id, **LIMITS)


@router.post(
    "/dictations/{dictation_id}/audio", response_model=CloudVoiceAudioResponse
)
async def cloud_voice_dictation_audio(
    dictation_id: str,
    body: CloudVoiceAudioRequest,
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> CloudVoiceAudioResponse:
    try:
        duration_ms = await _service(request).append(
            user.sub, dictation_id, body.seq, body.pcm16_b64
        )
    except CloudVoiceError as exc:
        _raise_mapped(exc)
    return CloudVoiceAudioResponse(seq=body.seq, duration_ms=duration_ms)


@router.post(
    "/dictations/{dictation_id}/finish", response_model=CloudVoiceResultResponse
)
async def cloud_voice_dictation_finish(
    dictation_id: str,
    body: CloudVoiceFinishRequest,
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> CloudVoiceResultResponse:
    try:
        result = await _service(request).finish(user.sub, dictation_id, body.last_seq)
    except CloudVoiceError as exc:
        _raise_mapped(exc)
    return CloudVoiceResultResponse(
        dictation_id=result.dictation_id,
        state=result.state,
        text=result.text,
        duration_ms=result.duration_ms,
    )


@router.delete("/dictations/{dictation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cloud_voice_dictation_delete(
    dictation_id: str,
    request: Request,
    user: UserTokenPayload = Depends(get_non_admin_api_user),
) -> Response:
    await _service(request).abort(user.sub, dictation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
