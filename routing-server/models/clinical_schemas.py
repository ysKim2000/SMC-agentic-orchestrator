from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Gender = Literal["M", "F"]
CareStatus = Literal["관찰중", "치료중", "추적관찰", "퇴원"]
AppointmentStatus = Literal["cancelled", "completed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewPatientForm(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    gender: Gender
    birth_date: date
    appt_date: date
    appt_time: time
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class AppointmentForm(StrictModel):
    appt_date: date
    appt_time: time
    note: str | None = Field(default=None, max_length=4000)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class AppointmentPatch(StrictModel):
    status: AppointmentStatus | None = None
    scheduled_date: date | None = None
    scheduled_time: time | None = None

    @model_validator(mode="after")
    def validate_change(self):
        has_schedule = self.scheduled_date is not None or self.scheduled_time is not None
        if has_schedule and (
            self.scheduled_date is None or self.scheduled_time is None
        ):
            raise ValueError("scheduled_date and scheduled_time must be provided together")
        if self.status is None and not has_schedule:
            raise ValueError("no appointment change supplied")
        if self.status is not None and has_schedule:
            raise ValueError("status and schedule cannot be changed together")
        return self


class PatientPatch(StrictModel):
    care_status: CareStatus


class DoctorSummary(BaseModel):
    employee_id: str
    name: str


class AnalysisQueueResponse(BaseModel):
    analysis_id: str
    status: str
    queue_position: int


class AnalysisImages(BaseModel):
    base: list[str] = Field(default_factory=list)
    heat: list[str] = Field(default_factory=list)
    box: list[str] = Field(default_factory=list)


class DicomMetadataItem(BaseModel):
    file_id: str
    original_filename: str
    metadata: dict = Field(default_factory=dict)


class AnalysisResponse(BaseModel):
    analysis_id: str
    patient_id: str
    visit_id: str
    appointment_id: str
    status: str
    queue_position: int | None
    attempt: int
    model_name: str | None
    risk_tier: str | None
    risk_status: str
    confidence: float | None
    finding: str | None
    interpretation: str | None
    recommendation: str | None
    predictions: object | None
    error: str | None
    images: AnalysisImages
    image_file_ids: AnalysisImages
    dicom: list[DicomMetadataItem] = Field(default_factory=list)
    dicom_metadata: list[DicomMetadataItem] = Field(default_factory=list)
    expires_at: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None


class AnalysisAccessUrlsResponse(BaseModel):
    images: AnalysisImages
    image_file_ids: AnalysisImages
    expires_at: str


class VisitHistoryItem(BaseModel):
    visit_id: str
    appointment_id: str
    visit_date: date
    exam_type: str
    status: str
    doctor: DoctorSummary
    latest_analysis_id: str | None
    analysis_status: str
    risk_tier: str | None
    dicom: list[DicomMetadataItem] = Field(default_factory=list)
    dicom_metadata: list[DicomMetadataItem] = Field(default_factory=list)


class VisitDetailResponse(BaseModel):
    visit_id: str
    appointment_id: str
    patient_id: str
    visit_date: date
    exam_type: str
    status: str
    doctor: DoctorSummary
    analyses: list[AnalysisResponse]


class ChatRequest(StrictModel):
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class ChatImageReference(BaseModel):
    token: str
    analysis_id: str
    file_id: str
    role: Literal["base", "heat", "box"]
    slice_index: int
    url: str
    expires_at: str


class ChatMessageResponse(BaseModel):
    message_id: str
    patient_id: str
    visit_id: str
    role: Literal["user", "assistant"]
    content: str
    context_analysis_ids: list[str] = Field(default_factory=list)
    images: list[ChatImageReference] = Field(default_factory=list)
    created_at: str


class ChatExchangeResponse(BaseModel):
    user: ChatMessageResponse
    assistant: ChatMessageResponse


class NoteCreate(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    visit_id: str | None = None

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class NoteResponse(BaseModel):
    note_id: str
    patient_id: str
    visit_id: str | None
    author_id: str
    author_name: str
    source: Literal["registration", "clinical"]
    text: str
    created_at: str


class MedicalFileMetadataResponse(BaseModel):
    file_id: str
    patient_id: str
    visit_id: str
    analysis_id: str
    kind: str
    role: str
    original_filename: str
    content_type: str
    extension: str
    size_bytes: int
    dicom_metadata: dict | None


class RegistrationResponse(BaseModel):
    patient_id: str
    appointment_id: str
    visit_id: str
    analysis: AnalysisQueueResponse | None
    visit_status: str = "open"
    analysis_status: str


class PatientSearchItem(BaseModel):
    patient_id: str
    name: str
    gender: Gender
    birth_date: date
    last_visit_date: date | None
    visit_count: int


class PatientSearchResponse(BaseModel):
    results: list[PatientSearchItem]


class PatientListItem(BaseModel):
    patient_id: str
    name: str
    gender: Gender
    birth_date: date
    appt_time: str
    exam_type: str
    care_status: CareStatus
    assigned_doctor: DoctorSummary | None
    appointment_doctor: DoctorSummary
    appointment_id: str
    latest_visit_id: str
    latest_analysis_id: str | None
    appointment_status: str
    analysis_status: str
    queue_position: int | None
    risk_tier: str | None
    confidence: float | None
    visit_count: int
    dicom: list[DicomMetadataItem] = Field(default_factory=list)
    dicom_metadata: list[DicomMetadataItem] = Field(default_factory=list)


class PatientListResponse(BaseModel):
    date: date
    patients: list[PatientListItem]


class PatientDetailResponse(BaseModel):
    patient_id: str
    name: str
    gender: Gender
    birth_date: date
    care_status: CareStatus
    assigned_doctor: DoctorSummary | None
    visit_count: int
    created_at: str
    dicom: list[DicomMetadataItem] = Field(default_factory=list)
    dicom_metadata: list[DicomMetadataItem] = Field(default_factory=list)


class AppointmentHistoryItem(BaseModel):
    appointment_id: str
    scheduled_at: str
    scheduled_date: date
    scheduled_time: str
    exam_type: str
    status: str
    doctor: DoctorSummary
    visit_id: str | None
    analysis_id: str | None
    analysis_status: str
    dicom: list[DicomMetadataItem] = Field(default_factory=list)
    dicom_metadata: list[DicomMetadataItem] = Field(default_factory=list)


class AppointmentPatchResponse(BaseModel):
    appointment_id: str
    status: str
    scheduled_at: str
    cancelled_analysis_ids: list[str] = Field(default_factory=list)
    running_analysis_ids: list[str] = Field(default_factory=list)
