import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from dependencies import (
    get_analysis_query_service,
    get_clinical_service,
    get_file_access_service,
    require_doctor,
)
from models.clinical_schemas import (
    AnalysisAccessUrlsResponse,
    AnalysisQueueResponse,
    AnalysisResponse,
    MedicalFileMetadataResponse,
    VisitDetailResponse,
    VisitHistoryItem,
)
from services.analysis_query_service import AnalysisQueryService
from services.clinical_service import ClinicalService, ClinicalServiceError
from services.file_access_service import FileAccessError, FileAccessService
from services.medical_file_service import MedicalFileError


visits_router = APIRouter()
analyses_router = APIRouter()
files_router = APIRouter()
patient_visits_router = APIRouter()


def _raise_service_error(exc):
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@patient_visits_router.get(
    "/{patient_id}/visits",
    response_model=list[VisitHistoryItem],
)
async def patient_visits(
    patient_id: str,
    doctor=Depends(require_doctor),
    service: AnalysisQueryService = Depends(get_analysis_query_service),
):
    try:
        return await service.patient_visits(patient_id, doctor)
    except ClinicalServiceError as exc:
        _raise_service_error(exc)


@patient_visits_router.get(
    "/{patient_id}/visits/{visit_id}",
    response_model=VisitDetailResponse,
)
async def visit_detail(
    patient_id: str,
    visit_id: str,
    doctor=Depends(require_doctor),
    service: AnalysisQueryService = Depends(get_analysis_query_service),
):
    try:
        return await service.visit_detail(patient_id, visit_id, doctor)
    except ClinicalServiceError as exc:
        _raise_service_error(exc)


@visits_router.post(
    "/{visit_id}/analyses",
    response_model=AnalysisQueueResponse,
    status_code=201,
)
async def enqueue_analysis(
    visit_id: str,
    query: str | None = Form(default=None, max_length=4000),
    files: list[UploadFile] = File(default=[]),
    doctor=Depends(require_doctor),
    service: ClinicalService = Depends(get_clinical_service),
):
    try:
        return await service.enqueue_visit_analysis(visit_id, query, files, doctor)
    except (ClinicalServiceError, MedicalFileError) as exc:
        _raise_service_error(exc)


@visits_router.get(
    "/{visit_id}/analyses",
    response_model=list[AnalysisResponse],
)
async def visit_analyses(
    visit_id: str,
    doctor=Depends(require_doctor),
    service: AnalysisQueryService = Depends(get_analysis_query_service),
):
    try:
        return await service.visit_analyses(visit_id, doctor)
    except ClinicalServiceError as exc:
        _raise_service_error(exc)


@analyses_router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: str,
    doctor=Depends(require_doctor),
    service: AnalysisQueryService = Depends(get_analysis_query_service),
):
    try:
        return await service.get_analysis(analysis_id, doctor)
    except ClinicalServiceError as exc:
        _raise_service_error(exc)


@analyses_router.post(
    "/{analysis_id}/access-urls",
    response_model=AnalysisAccessUrlsResponse,
)
async def renew_access_urls(
    analysis_id: str,
    doctor=Depends(require_doctor),
    service: FileAccessService = Depends(get_file_access_service),
):
    try:
        return await service.issue_analysis_urls(analysis_id, doctor)
    except FileAccessError as exc:
        _raise_service_error(exc)


@files_router.get("/{file_id}")
async def stream_file(
    file_id: str,
    exp: int = Query(),
    sig: str = Query(min_length=64, max_length=64),
    service: FileAccessService = Depends(get_file_access_service),
):
    try:
        metadata, stream = await service.open_file(file_id, exp, sig)
    except FileAccessError as exc:
        _raise_service_error(exc)

    async def chunks():
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                break
            yield chunk

    max_age = max(0, min(300, exp - int(time.time())))
    return StreamingResponse(
        chunks(),
        media_type=metadata["content_type"],
        headers={
            "Content-Length": str(metadata["size_bytes"]),
            "ETag": f'"{metadata["sha256"]}"',
            "Cache-Control": f"private, max-age={max_age}",
            "Content-Disposition": "inline",
        },
    )


@files_router.get(
    "/{file_id}/metadata",
    response_model=MedicalFileMetadataResponse,
)
async def get_file_metadata(
    file_id: str,
    doctor=Depends(require_doctor),
    service: FileAccessService = Depends(get_file_access_service),
):
    try:
        return await service.get_metadata(file_id, doctor)
    except FileAccessError as exc:
        _raise_service_error(exc)
