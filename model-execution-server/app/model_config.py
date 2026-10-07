"""모델 config.yaml 로더.

/app/models/{model_name}/config.yaml 을 읽어 dict로 반환합니다.
MODELS_DIR 환경변수로 경로를 오버라이드할 수 있습니다.
"""
import os
from pathlib import Path
from typing import Any

import yaml

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/app/models"))
REQUIRED_FIELDS = (
    "model_name",
    "execution_mode",
    "runtime",
    "runner_path",
    "inference_path",
    "model_path",
)


def _models_dir() -> Path:
    """Read MODELS_DIR at call time so tests and deployments can override it."""
    return Path(os.environ.get("MODELS_DIR", str(MODELS_DIR)))


def load_model_config(model_name: str) -> dict[str, Any]:
    # A model name is an identifier, not a path supplied by the caller.
    if not model_name or Path(model_name).name != model_name:
        raise FileNotFoundError(f"Model config not found for '{model_name}'")
    config_path = _models_dir() / model_name / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Model config not found: {config_path}. "
            f"Create models/{model_name}/config.yaml or set MODELS_DIR."
        )
    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError(f"Invalid config format: {config_path}")
    return config


def list_models() -> list[str]:
    models_dir = _models_dir()
    if not models_dir.exists():
        return []
    return sorted(d.name for d in models_dir.iterdir() if (d / "config.yaml").exists())


def _container_path_accessible(path_value: object, *, require_file: bool) -> bool:
    if not isinstance(path_value, str) or not path_value.startswith("/app/"):
        return False
    path = Path(path_value)
    if path.exists():
        return True

    # During local validation, translate container mount paths to the repository.
    repo_root = Path(__file__).resolve().parents[1]
    local_path = repo_root / path.relative_to("/app")
    if local_path.exists():
        return True
    # Model weights are deployment artifacts and may be populated into an existing
    # mounted model directory after image build.
    return not require_file and local_path.parent.exists()


def validate_all_model_configs() -> dict[str, Any]:
    """Validate every model without silently skipping broken entries."""
    errors: list[dict[str, str]] = []
    valid_names: set[str] = set()
    seen_names: set[str] = set()
    model_names = list_models()

    from app.runtime_loader import SUPPORTED_RUNTIMES

    for directory_name in model_names:
        try:
            config = load_model_config(directory_name)
        except (OSError, ValueError) as exc:
            errors.append({"model_name": directory_name, "error_type": "invalid_config", "message": str(exc)})
            continue

        missing = [field for field in REQUIRED_FIELDS if not config.get(field)]
        if missing:
            errors.append({
                "model_name": directory_name,
                "error_type": "missing_fields",
                "message": f"Missing required fields: {', '.join(missing)}",
            })
            continue
        declared_name = str(config["model_name"])
        if declared_name in seen_names:
            errors.append({
                "model_name": declared_name,
                "error_type": "duplicate_model",
                "message": "Duplicate model_name",
            })
            continue
        seen_names.add(declared_name)
        if declared_name != directory_name:
            errors.append({
                "model_name": directory_name,
                "error_type": "model_name_mismatch",
                "message": f"Directory name does not match model_name '{declared_name}'",
            })
            continue
        if config["runtime"] not in SUPPORTED_RUNTIMES:
            errors.append({
                "model_name": declared_name,
                "error_type": "unsupported_runtime",
                "message": f"Unsupported runtime '{config['runtime']}'",
            })
            continue
        inaccessible = [
            field
            for field in ("runner_path", "inference_path", "model_path")
            if not _container_path_accessible(config[field], require_file=field != "model_path")
        ]
        if inaccessible:
            errors.append({
                "model_name": declared_name,
                "error_type": "inaccessible_path",
                "message": f"Inaccessible paths: {', '.join(inaccessible)}",
            })
            continue
        valid_names.add(declared_name)

    return {
        "total": len(model_names),
        "valid": len(valid_names),
        "invalid": len(errors),
        "valid_models": valid_names,
        "errors": errors,
    }
