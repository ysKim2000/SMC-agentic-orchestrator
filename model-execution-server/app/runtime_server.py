"""Generic runtime container server.

각 runtime 컨테이너에서 실행되는 FastAPI 서버입니다.
모델별 runner.py 를 동적 로드하고 POST /run/v2 로 추론을 실행합니다.
"""
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.model_config import load_model_config, list_models
from app.runner_loader import run_predict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("maple.runtime")

app = FastAPI(title="maple-platform Runtime Server", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INPUTS_DIR = Path(os.environ.get("INPUTS_DIR", "/app/inputs"))
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", "/app/outputs"))


class RunV2Request(BaseModel):
    model_name: str
    input_path: str
    output_dir: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class RunV2Response(BaseModel):
    status: str
    model_name: str
    result: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "maple-runtime-server",
        "models": list_models(),
    }


@app.get("/models")
async def models():
    return {"models": list_models()}


@app.post("/run/v2", response_model=RunV2Response)
async def run_v2(req: RunV2Request):
    logger.info("RunV2 request - model_name=%s input_path=%s", req.model_name, req.input_path)

    try:
        config = load_model_config(req.model_name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    config.update(req.params)
    config.setdefault("model_name", req.model_name)

    output_dir = req.output_dir or str(OUTPUTS_DIR / req.model_name)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    try:
        result = run_predict(config, req.input_path, output_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AttributeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except TypeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Runner error - model_name=%s: %s", req.model_name, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Runner error: {exc}") from exc

    return RunV2Response(
        status="ok",
        model_name=req.model_name,
        result=result,
    )
