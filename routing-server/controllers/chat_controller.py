from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_clinical_chat_service, require_doctor
from models.clinical_schemas import (
    ChatExchangeResponse,
    ChatMessageResponse,
    ChatRequest,
)
from services.clinical_chat_service import ClinicalChatService
from services.clinical_service import ClinicalServiceError


router = APIRouter()


def _raise_service_error(exc: ClinicalServiceError):
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{visit_id}/chat", response_model=list[ChatMessageResponse])
async def get_chat(
    visit_id: str,
    doctor=Depends(require_doctor),
    service: ClinicalChatService = Depends(get_clinical_chat_service),
):
    try:
        return await service.get_chat(visit_id, doctor)
    except ClinicalServiceError as exc:
        _raise_service_error(exc)


@router.post("/{visit_id}/chat", response_model=ChatExchangeResponse)
async def send_chat(
    visit_id: str,
    body: ChatRequest,
    doctor=Depends(require_doctor),
    service: ClinicalChatService = Depends(get_clinical_chat_service),
):
    try:
        return await service.send(visit_id, body.content, doctor)
    except ClinicalServiceError as exc:
        _raise_service_error(exc)
