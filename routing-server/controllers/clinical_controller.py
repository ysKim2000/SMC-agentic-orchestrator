from datetime import date, time
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from dependencies import get_clinical_service, require_doctor
from models.clinical_schemas import (
    AppointmentForm,
    AppointmentHistoryItem,
    AppointmentPatch,
    AppointmentPatchResponse,
    NewPatientForm,
    PatientDetailResponse,
    PatientListResponse,
    PatientPatch,
    PatientSearchResponse,
    RegistrationResponse,
)
from services.clinical_service import ClinicalService, ClinicalServiceError
from services.medical_file_service import MedicalFileError


patients_router = APIRouter()
appointments_router = APIRouter()


def _raise_http(exc: ClinicalServiceError | MedicalFileError):
    detail = exc.detail
    if isinstance(exc, ClinicalServiceError) and exc.extra:
        detail = {"message": exc.detail, **exc.extra}
    raise HTTPException(status_code=exc.status_code, detail=detail) from exc


def _raise_validation(exc: ValidationError):
    raise RequestValidationError(exc.errors()) from exc


@patients_router.get("/search", response_model=PatientSearchResponse)
async def search_patients(
    q: str = Query(min_length=1, max_length=100),
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    return await service.search_patients(q, doctor)


@patients_router.get("", response_model=PatientListResponse)
async def list_patients(
    date_: date = Query(alias="date"),
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    return await service.list_patients(date_, doctor)


@patients_router.post("", response_model=RegistrationResponse, status_code=201)
async def register_patient(
    name: str = Form(),
    gender: Literal["M", "F"] = Form(),
    birth_date: date = Form(),
    appt_date: date = Form(),
    appt_time: time = Form(),
    note: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    try:
        request = NewPatientForm(
            name=name,
            gender=gender,
            birth_date=birth_date,
            appt_date=appt_date,
            appt_time=appt_time,
            note=note,
        )
        return await service.register_new(request, files, doctor)
    except ValidationError as exc:
        _raise_validation(exc)
    except (ClinicalServiceError, MedicalFileError) as exc:
        _raise_http(exc)


@patients_router.get("/{patient_id}", response_model=PatientDetailResponse)
async def get_patient(
    patient_id: str,
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    try:
        return await service.get_patient_detail(patient_id, doctor)
    except ClinicalServiceError as exc:
        _raise_http(exc)


@patients_router.patch("/{patient_id}", response_model=PatientDetailResponse)
async def patch_patient(
    patient_id: str,
    patch: PatientPatch,
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    try:
        return await service.update_patient(patient_id, patch.care_status, doctor)
    except ClinicalServiceError as exc:
        _raise_http(exc)


@patients_router.post(
    "/{patient_id}/appointments",
    response_model=RegistrationResponse,
    status_code=201,
)
async def register_revisit(
    patient_id: str,
    appt_date: date = Form(),
    appt_time: time = Form(),
    note: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    try:
        request = AppointmentForm(
            appt_date=appt_date,
            appt_time=appt_time,
            note=note,
        )
        return await service.register_revisit(patient_id, request, files, doctor)
    except ValidationError as exc:
        _raise_validation(exc)
    except (ClinicalServiceError, MedicalFileError) as exc:
        _raise_http(exc)


@patients_router.get(
    "/{patient_id}/appointments",
    response_model=list[AppointmentHistoryItem],
)
async def appointment_history(
    patient_id: str,
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    try:
        return await service.appointment_history(patient_id, doctor)
    except ClinicalServiceError as exc:
        _raise_http(exc)


@appointments_router.patch(
    "/{appointment_id}",
    response_model=AppointmentPatchResponse,
)
async def patch_appointment(
    appointment_id: str,
    patch: AppointmentPatch,
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    try:
        return await service.patch_appointment(appointment_id, patch, doctor)
    except ClinicalServiceError as exc:
        _raise_http(exc)
