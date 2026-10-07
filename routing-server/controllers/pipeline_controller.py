import os
import json
import logging
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from services.pipeline_service import PipelineService
from dependencies import get_pipeline_service

router = APIRouter(tags=["pipeline"])
logger = logging.getLogger("maple.pipeline.controller")

SAVE_BASE_INPUT_DIR  = "./data/input"
SAVE_BASE_OUTPUT_DIR = "./data/output"


class PipelineStep(BaseModel):
    step:       int
    model:      str
    department: str
    project:    str


class PipelineRequest(BaseModel):
    steps: list[PipelineStep]


@router.post("/run")
async def run_pipeline(
    steps:            str = Form(...),   # JSON 문자열로 전달
    pipeline_service: PipelineService = Depends(get_pipeline_service),
    file:             UploadFile = File(...),
):
    """
    Agent의 Execution Plan을 받아 파이프라인 순차 실행.

    Form:
      - steps: JSON 문자열 (PipelineStep 리스트)
        예: '[{"step":1,"model":"YOLOv12","department":"Rheumatology","project":"SI Joints Detection"},
              {"step":2,"model":"GradCAM++","department":"Rheumatology","project":"BME Classification"}]'
      - file: DICOM 파일

    Returns:
      - step_results: 각 Step의 결과 (image_b64, predictions 등)
    """
    try:
        steps_data = json.loads(steps)
    except Exception:
        return JSONResponse({"status": "error", "message": "steps 파싱 실패. JSON 형식인지 확인하세요."})

    # DICOM 파일 저장
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_input_dir  = Path(SAVE_BASE_INPUT_DIR)  / f"pipeline/{timestamp}"
    save_output_dir = Path(SAVE_BASE_OUTPUT_DIR) / f"pipeline/{timestamp}"
    save_input_dir.mkdir(parents=True, exist_ok=True)

    file_bytes = await file.read()
    save_path  = save_input_dir / file.filename
    save_path.write_bytes(file_bytes)
    image_path = str(save_path.resolve())

    logger.info(
        "[Client] 파이프라인 입력: steps=%s개 | file=%s | content_type=%s | size=%s | input_path=%s",
        len(steps_data),
        file.filename,
        file.content_type,
        len(file_bytes),
        save_path,
    )

    result = await pipeline_service.run_pipeline(
        steps          = steps_data,
        image_path     = image_path,
        save_output_dir= save_output_dir,
    )

    return JSONResponse(content=result)
