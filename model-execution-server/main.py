import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.model_config import REQUIRED_FIELDS, load_model_config, validate_all_model_configs
from app.runtime_loader import (
    RuntimeConfigError,
    list_runtimes,
    resolve_runtime_url,
    validate_http_url,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("maple.inference")


class InferRequest(BaseModel):
    model_id: str
    input_data: Any
    params: dict[str, Any] = Field(default_factory=dict)


class InferResponse(BaseModel):
    status: str = "ok"
    result: Any = None
    output_images: list[str] = Field(default_factory=list)
    model_output: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="maple Model Execution Server — Inference Gateway", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _request_timeout(
    params: dict[str, Any],
    model_config: dict[str, Any] | None = None,
) -> float:
    """Resolve timeout without coupling the legacy endpoint to v2 config."""
    configured_default = (model_config or {}).get("timeout", 120.0)
    return float(params.get("timeout", configured_default))


def _config_for_params(params: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort model config lookup for the legacy endpoint.

    The legacy request carries no model name of its own, but the routing server puts the
    package directory name in params. A miss is not an error here - the caller falls back
    to the default timeout.
    """
    name = params.get("model_name")
    if not isinstance(name, str) or not name:
        return None
    try:
        return load_model_config(name)
    except (FileNotFoundError, ValueError):
        return None


def _build_container_payload(input_data: Any, params: dict[str, Any]) -> dict[str, Any]:
    model_name = params.get("model_name", "")
    extra_params: dict[str, Any] = {}

    if isinstance(input_data, str):
        input_path = input_data
    elif isinstance(input_data, dict) and "roi" in input_data:
        input_path = input_data.get("image_path", "")
        extra_params["roi"] = input_data["roi"]
    elif isinstance(input_data, list):
        input_path = ""
        extra_params["data"] = input_data
    else:
        input_path = ""
        extra_params["data"] = input_data

    model_path = params.get("model_path")
    if model_path:
        extra_params["model_path"] = model_path

    extra_params.update(params.get("container_payload") or {})

    payload: dict[str, Any] = {
        "model_name": model_name,
        "input_path": input_path,
    }
    if extra_params:
        payload["params"] = extra_params
    return payload


def _normalize_container_response(model_id: str, raw: dict[str, Any]) -> InferResponse:
    # Runtime v2 containers return {"status": "ok", "model_name": "...", "result": {...}}
    # Legacy flat-response containers return a flat dict with image_b64 / result_type at top level
    runner_data = raw.get("result") if isinstance(raw.get("result"), dict) else raw

    images = runner_data.get("images_b64") or []
    if not images and runner_data.get("image_b64"):
        images = [runner_data["image_b64"]]

    model_output = {
        key: value
        for key, value in runner_data.items()
        if key not in ("status", "result_type", "predictions", "image_b64", "images_b64")
    }

    return InferResponse(
        result={
            "result_type": runner_data.get("result_type") or "unknown",
            "predictions": runner_data.get("predictions") or [],
            "data": runner_data.get("data") or {},
        },
        output_images=images,
        model_output=model_output,
        metadata={
            "model_id": model_id,
            "container_status": raw.get("status"),
            "result_type": runner_data.get("result_type"),
        },
    )


def _normalize_v2_response(
    model_name: str,
    runtime_name: str,
    raw: dict[str, Any],
) -> InferResponse:
    response = _normalize_container_response(model_name, raw)
    response.metadata = {
        "model_name": model_name,
        "runtime": runtime_name,
    }
    return response


def _error_detail(
    model_name: str,
    runtime: str | None,
    error_type: str,
    message: str,
) -> dict[str, Any]:
    return {
        "model_name": model_name,
        "runtime": runtime,
        "error_type": error_type,
        "message": message,
    }


def _resolve_container_url(container_url: str) -> str:
    """localhost URL을 환경변수에 정의된 Docker 서비스 URL로 교체.

    로컬 개발 환경에서 container_url 이 localhost:PORT 로 들어올 때
    RUNTIME_*_URL 환경변수가 설정되어 있으면 해당 URL 로 교체합니다.
    Docker 네트워크 내부에서는 서비스명으로 직접 통신하므로 동작하지 않습니다.
    """
    parsed = urlparse(container_url)
    if parsed.hostname not in ("localhost", "127.0.0.1"):
        return container_url

    port_map: dict[int | None, str | None] = {
        9020: os.getenv("RUNTIME_BASIC_URL"),
        9021: os.getenv("RUNTIME_MEDICAL_URL"),
        9022: os.getenv("RUNTIME_YOLO_URL"),
        9023: os.getenv("RUNTIME_NNUNET_URL"),
    }
    replacement = port_map.get(parsed.port)
    return replacement.rstrip("/") if replacement else container_url


@app.get("/health")
async def health():
    return {"status": "ok", "service": "inference-gateway"}


@app.on_event("startup")
async def validate_models_on_startup() -> None:
    report = validate_all_model_configs()
    app.state.model_validation = report
    if report["invalid"]:
        logger.error(
            "Model configuration validation failed: valid=%s invalid=%s errors=%s",
            report["valid"],
            report["invalid"],
            report["errors"],
        )
    else:
        logger.info("Model configuration validation passed: %s models", report["valid"])


@app.get("/ready")
async def ready():
    report = validate_all_model_configs()
    app.state.model_validation = report
    runtime_states: dict[str, str] = {}
    errors = list(report["errors"])

    for runtime_name, runtime_url in list_runtimes().items():
        if not runtime_url:
            runtime_states[runtime_name] = "invalid_config"
            errors.append({
                "runtime": runtime_name,
                "error_type": "runtime_url_invalid",
                "message": "Runtime URL is missing or invalid",
            })
            continue
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{runtime_url}/health")
                response.raise_for_status()
            runtime_states[runtime_name] = "ready"
        except httpx.TimeoutException:
            runtime_states[runtime_name] = "timeout"
            errors.append({"runtime": runtime_name, "error_type": "runtime_timeout", "message": "Health check timed out"})
        except (httpx.HTTPError, ValueError):
            runtime_states[runtime_name] = "unavailable"
            errors.append({"runtime": runtime_name, "error_type": "runtime_unavailable", "message": "Health check failed"})

    is_ready = not errors
    body = {
        "status": "ready" if is_ready else "not_ready",
        "models": {key: report[key] for key in ("total", "valid", "invalid")},
        "runtimes": runtime_states,
        "errors": errors,
    }
    if not is_ready:
        return JSONResponse(status_code=503, content=body)
    return body


@app.post("/infer", response_model=InferResponse)
async def infer(req: InferRequest):
    container_url = (req.params.get("container_url") or "").rstrip("/")
    container_endpoint = req.params.get("container_endpoint", "/run")
    # The routing server still calls this legacy endpoint, so a model that declares
    # `timeout:` in its config.yaml has to be honoured here too - otherwise the 120 s
    # default silently cuts off the long-running 3D segmenters (chest CT nnU-Net runs
    # past two and a half minutes) while the container keeps working on the request.
    timeout = _request_timeout(req.params, _config_for_params(req.params))

    logger.warning("Legacy POST /infer used - model_id=%s", req.model_id)
    if not validate_http_url(container_url):
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                req.model_id,
                None,
                "invalid_container_url",
                "params.container_url must be an absolute HTTP(S) URL",
            ),
        )
    if not container_endpoint.startswith("/"):
        container_endpoint = f"/{container_endpoint}"
    container_url = _resolve_container_url(container_url)
    if not validate_http_url(container_url):
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                req.model_id,
                None,
                "invalid_container_url",
                "Resolved params.container_url must be an absolute HTTP(S) URL",
            ),
        )

    payload = _build_container_payload(req.input_data, req.params)
    logger.info("Infer request - model_id=%s target=%s%s", req.model_id, container_url, container_endpoint)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{container_url}{container_endpoint}", json=payload)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.TimeoutException as exc:
        logger.error("Container timeout - model_id=%s: %s", req.model_id, exc)
        raise HTTPException(status_code=504, detail=f"Container timeout: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        logger.error("Container HTTP error - model_id=%s: %s", req.model_id, exc)
        raise HTTPException(status_code=502, detail=f"Container HTTP error: {exc}") from exc
    except httpx.HTTPError as exc:
        logger.error("Container request failed - model_id=%s: %s", req.model_id, exc)
        raise HTTPException(status_code=502, detail=f"Container request failed: {exc}") from exc

    if raw.get("status") != "ok":
        detail = raw.get("detail") or raw.get("message") or "Container inference failed"
        raise HTTPException(status_code=502, detail=detail)

    return _normalize_container_response(req.model_id, raw)


class InferV2Request(BaseModel):
    model_name: str
    input_path: str
    output_dir: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


@app.post("/infer/v2")
async def infer_v2(req: InferV2Request):
    """Runtime-image-based inference. model_name → config.yaml → runtime container → /run/v2."""
    try:
        config = load_model_config(req.model_name)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_error_detail(req.model_name, None, "model_not_found", str(exc)),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(req.model_name, None, "invalid_model_config", str(exc)),
        ) from exc

    runtime_name = config.get("runtime")
    if not runtime_name:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(req.model_name, None, "missing_runtime", "config.yaml is missing 'runtime'"),
        )
    missing_fields = [field for field in REQUIRED_FIELDS if not config.get(field)]
    if missing_fields:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                req.model_name,
                runtime_name,
                "missing_model_fields",
                f"Missing required fields: {', '.join(missing_fields)}",
            ),
        )
    if config["model_name"] != req.model_name:
        raise HTTPException(
            status_code=422,
            detail=_error_detail(
                req.model_name,
                runtime_name,
                "model_name_mismatch",
                "config.yaml model_name does not match its directory",
            ),
        )
    startup_report = getattr(app.state, "model_validation", None)
    if startup_report:
        model_error = next(
            (
                error
                for error in startup_report["errors"]
                if error.get("model_name") == req.model_name
            ),
            None,
        )
        if model_error:
            raise HTTPException(
                status_code=422,
                detail=_error_detail(
                    req.model_name,
                    runtime_name,
                    model_error["error_type"],
                    model_error["message"],
                ),
            )

    try:
        runtime_url = resolve_runtime_url(runtime_name)
    except RuntimeConfigError as exc:
        status_code = 422 if exc.error_type == "unsupported_runtime" else 503
        raise HTTPException(
            status_code=status_code,
            detail=_error_detail(req.model_name, runtime_name, exc.error_type, str(exc)),
        ) from exc

    safe_params = {
        key: value
        for key, value in req.params.items()
        if key not in {"container_url", "container_endpoint"}
    }
    payload = {
        "model_name": req.model_name,
        "input_path": req.input_path,
        "output_dir": req.output_dir,
        "params": safe_params,
    }
    timeout = _request_timeout(req.params, config)
    target = f"{runtime_url}/run/v2"
    logger.info(
        "InferV2 request - model_name=%s runtime=%s target=%s",
        req.model_name,
        runtime_name,
        target,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(target, json=payload)
            resp.raise_for_status()
            raw = resp.json()
    except httpx.TimeoutException as exc:
        logger.error("Runtime timeout - model_name=%s: %s", req.model_name, exc)
        raise HTTPException(
            status_code=504,
            detail=_error_detail(req.model_name, runtime_name, "runtime_timeout", "Runtime request timed out"),
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error("Runtime HTTP error - model_name=%s: %s", req.model_name, exc)
        raise HTTPException(
            status_code=502,
            detail=_error_detail(req.model_name, runtime_name, "runtime_http_error", "Runtime returned an error"),
        ) from exc
    except httpx.HTTPError as exc:
        logger.error("Runtime request failed - model_name=%s: %s", req.model_name, exc)
        raise HTTPException(
            status_code=502,
            detail=_error_detail(req.model_name, runtime_name, "runtime_connection_failed", "Runtime connection failed"),
        ) from exc

    if raw.get("status") != "ok":
        detail = raw.get("detail") or raw.get("message") or "Runtime inference failed"
        raise HTTPException(
            status_code=502,
            detail=_error_detail(req.model_name, runtime_name, "runtime_inference_failed", str(detail)),
        )

    return _normalize_v2_response(req.model_name, runtime_name, raw)
