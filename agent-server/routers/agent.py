import time
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from services import agent_service
from services.experiment_logger import append_experiment_record

router = APIRouter()


class Attachment(BaseModel):
    """라우팅 서버가 정규화한 첨부파일 하나.
    (input-normalization-design.md §3 계약: attachments[])"""
    type: str = ""                                            # dicom | nifti | image | csv | pdf ...
    filename: str = ""
    images: list[str] = Field(default_factory=list)           # VLM 입력 이미지 (data URI). 없으면 []
    text: str = ""                                            # VLM 입력 텍스트 (ASR/OCR/추출). 없으면 ""
    metadata: dict = Field(default_factory=dict)              # 구조화·비식별화 메타데이터
    tabular: list[dict] | None = None                        # CSV면 dict 리스트, 아니면 null

    @field_validator("images", mode="before")
    @classmethod
    def _images_none_to_list(cls, value):
        return [] if value is None else value

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_none_to_dict(cls, value):
        return {} if value is None else value


class PlanRequest(BaseModel):
    query: str
    uploaded_types: list[str] = Field(default_factory=list)   # ["dicom", "csv", ...] 업로드된 파일 타입 목록
    history: list[dict] = Field(default_factory=list)
    mode: Literal["auto", "clinical", "prediction", "general"] = "auto"
    # general 모드용 데이터 (백엔드가 변환해서 넘김)
    images: list[str] = Field(default_factory=list)           # (레거시) DICOM/NIfTI → PNG 변환 후 base64
    csv_data: list[dict] = Field(default_factory=list)        # (레거시) CSV → pd.read_csv().to_dict() 결과
    attachments: list[Attachment] = Field(default_factory=list)  # 정규화 첨부파일 (레거시 images/csv_data 대체)

    @field_validator("uploaded_types", "history", "images", "csv_data", "attachments", mode="before")
    @classmethod
    def normalize_optional_lists(cls, value):
        return [] if value is None else value


class ImageResult(BaseModel):
    role: str = ""   # bbox_overlay | gradcam_overlay | segmentation_overlay
    data: str = ""   # data:image/png;base64,...

    @field_validator("role", "data", mode="before")
    @classmethod
    def _none_to_str(cls, value):
        return "" if value is None else value


class StepResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    step: int | str        # prediction=순번(int), general DAG=step_id(str "s1")
    model: str = ""
    task_type: str = ""
    result_type: str = ""  # 컨테이너가 안 주면 null로 올 수 있음 → "" 흡수
    predictions: Any = Field(default_factory=list)
    evidence_role: str = ""                              # primary | supporting (계획 단계에서 LLM이 지정)
    model_output: Any = Field(default_factory=dict)      # ROI 좌표, 분류 상세, segmentation 메타, raw text 등
    images: list[ImageResult | dict | str] = Field(default_factory=list)

    @field_validator("model", "task_type", mode="before")
    @classmethod
    def _none_to_str(cls, value):
        return "" if value is None else value

    @field_validator("result_type", mode="before")
    @classmethod
    def _result_type_to_str(cls, value):
        # 컨테이너/모델메타가 null 또는 리스트로 보내도 문자열로 정규화
        if value is None:
            return ""
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return value

    @field_validator("images", mode="before")
    @classmethod
    def normalize_images(cls, value):
        return [] if value is None else value


class TaskInfo(BaseModel):
    department: str
    project: str
    # Optional single-target reporting controls. These contain the requested
    # finding and model evidence only; reference labels are never accepted.
    evaluation_scope: str | None = None
    report_policy: dict = Field(default_factory=dict)
    target_evidence_summary: list[dict] = Field(default_factory=list)


class ExecutionContext(BaseModel):
    mode: str
    plan: dict = {}              # /agent/plan이 반환한 execution_plan
    attachments_meta: list[dict] = Field(default_factory=list)   # 원본 메타 (전환기)
    attachments: list[dict] = Field(default_factory=list)        # 원본 스캔 (이미지+메타)


class InterpretRequest(BaseModel):
    query: str
    task: TaskInfo = TaskInfo(department="", project="")
    execution_context: ExecutionContext = ExecutionContext(mode="prediction")
    step_results: list[StepResult]


# ── Clinical Board 응답 스키마 (v4 2.6) ─────────────────────────────────────────

class BoardReader(BaseModel):
    finding: str = ""
    interpretation: str = ""
    recommendation: str = ""
    claims: list[dict] = Field(default_factory=list)


class BoardChallenger(BaseModel):
    differential: list[dict] = Field(default_factory=list)
    source: Literal["alt_model", "blind_same_model"] = "blind_same_model"


class BoardEvidence(BaseModel):
    evidence_map: list[dict] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


class BoardGuardian(BaseModel):
    veto: bool = False
    risk_tier: Literal["low", "moderate", "high", "critical"] = "moderate"
    flags: list[str] = Field(default_factory=list)
    rationale: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class BoardCalibration(BaseModel):
    agreement_score: float | None = None
    score_gap: float | None = None
    model_conflict: bool = False


class Board(BaseModel):
    reader: BoardReader = Field(default_factory=BoardReader)
    challenger: BoardChallenger = Field(default_factory=BoardChallenger)
    evidence: BoardEvidence = Field(default_factory=BoardEvidence)
    guardian: BoardGuardian = Field(default_factory=BoardGuardian)
    calibration: BoardCalibration = Field(default_factory=BoardCalibration)


class ConfidenceScore(BaseModel):
    model: str = ""
    task_type: str = ""
    label: str = ""
    score: float


class Confidence(BaseModel):
    display: float | None = None
    source_model: str | None = None
    task_type: str | None = None
    model_scores: list[ConfidenceScore] = Field(default_factory=list)
    conflict: bool = False
    score_gap: float | None = None


class InterpretResult(BaseModel):
    finding: str = ""
    interpretation: str = ""
    recommendation: str = ""
    risk_tier: Literal["low", "moderate", "high", "critical"] = "moderate"
    confidence: Confidence = Field(default_factory=Confidence)
    # Reader가 낸 "질의 대상 소견이 존재할 확률"(0-100). 서비스 내부에서는 계산되지만
    # 응답 스키마에 없어 잘려 나갔다. 소견 판정 평가(AUROC)는 이 값을 쓴다.
    reader_confidence_0_100: int | None = None


class InterpretResponse(BaseModel):
    status: Literal["confirmed", "pending_review"]
    result: InterpretResult = Field(default_factory=InterpretResult)
    interpretation: str = ""
    interpretation_raw: str = ""
    board: Board = Field(default_factory=Board)
    escalation_reason: str | None = None
    images: dict = Field(default_factory=dict)


@router.post("/plan")
async def plan(req: PlanRequest):
    # RSNA QI EXPERIMENT LOGGING START: timing-only side effect; safe to remove after study.
    started = time.perf_counter()
    # RSNA QI EXPERIMENT LOGGING END

    # prediction 모드에서 파일 없이 텍스트만 오는 경우 차단
    if req.mode == "prediction" and not req.uploaded_types and not req.attachments:
        result = {
            "status": "requires_input",
            "mode": "prediction",
            "message": (
                "AI 예측 모드는 분석할 의료 데이터(DICOM 등)가 필요합니다. "
                "파일을 첨부하거나, 의학 지식 질문은 임상 모드(clinical)로 변경해 주세요."
            ),
        }
    else:
        result = await agent_service.plan(
            query=req.query,
            uploaded_types=req.uploaded_types,
            history=req.history,
            mode=req.mode,
            images=req.images,
            csv_data=req.csv_data,
            attachments=[a.model_dump() for a in req.attachments],
        )

    # RSNA QI EXPERIMENT LOGGING START: remove this block after study if desired.
    steps = result.get("execution_plan", {}).get("steps", []) if isinstance(result, dict) else []
    append_experiment_record({
        "endpoint": "plan",
        "query": req.query,
        "mode": req.mode,
        "uploaded_types": req.uploaded_types,
        "latency_sec": round(time.perf_counter() - started, 4),
        "status": result.get("status") if isinstance(result, dict) else None,
        "response_mode": result.get("mode") if isinstance(result, dict) else None,
        "selected_model": steps[0].get("model") if steps else None,
        "step_count": len(steps),
    })
    # RSNA QI EXPERIMENT LOGGING END

    return result


@router.post("/interpret", response_model=InterpretResponse)
async def interpret(req: InterpretRequest):
    # RSNA QI EXPERIMENT LOGGING START: timing-only side effect; safe to remove after study.
    started = time.perf_counter()
    result = await agent_service.interpret(
        query=req.query,
        task=req.task.model_dump(),
        execution_context=req.execution_context.model_dump(),
        step_results=[s.model_dump() for s in req.step_results],
    )
    interpretation = result.get("interpretation", "") if isinstance(result, dict) else ""
    append_experiment_record({
        "endpoint": "interpret",
        "query": req.query,
        "latency_sec": round(time.perf_counter() - started, 4),
        "task": req.task.model_dump(),
        "step_models": [s.model for s in req.step_results],
        "interpretation_generated": bool(interpretation),
        "xai_marker_included": "[IMG:" in interpretation,
        "image_count": len(result.get("images", {})) if isinstance(result, dict) else 0,
    })
    # RSNA QI EXPERIMENT LOGGING END

    return result
