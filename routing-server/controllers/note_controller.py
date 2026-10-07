from fastapi import APIRouter, Depends, HTTPException

from dependencies import get_clinical_note_service, require_doctor
from models.clinical_schemas import NoteCreate, NoteResponse
from services.clinical_note_service import ClinicalNoteService
from services.clinical_service import ClinicalServiceError


router = APIRouter()


def _raise_service_error(exc: ClinicalServiceError):
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get("/{patient_id}/notes", response_model=list[NoteResponse])
async def list_notes(
    patient_id: str,
    doctor=Depends(require_doctor),
    service: ClinicalNoteService = Depends(get_clinical_note_service),
):
    try:
        return await service.list_notes(patient_id, doctor)
    except ClinicalServiceError as exc:
        _raise_service_error(exc)


@router.post(
    "/{patient_id}/notes",
    response_model=NoteResponse,
    status_code=201,
)
async def create_note(
    patient_id: str,
    body: NoteCreate,
    doctor=Depends(require_doctor),
    service: ClinicalNoteService = Depends(get_clinical_note_service),
):
    try:
        return await service.create_note(
            patient_id,
            body.text,
            body.visit_id,
            doctor,
        )
    except ClinicalServiceError as exc:
        _raise_service_error(exc)
