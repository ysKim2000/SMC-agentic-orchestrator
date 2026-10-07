"""Runtime container URL resolver.

config.yaml 의 runtime 필드를 보고 어느 컨테이너로 요청을 보낼지 결정합니다.
각 runtime 의 URL 은 환경변수로 주입됩니다.
"""
import os
from urllib.parse import urlparse

_RUNTIME_ENV: dict[str, str] = {
    "runtime-basic":   "RUNTIME_BASIC_URL",
    "runtime-medical": "RUNTIME_MEDICAL_URL",
    "runtime-yolo":    "RUNTIME_YOLO_URL",
    "runtime-nnunet":  "RUNTIME_NNUNET_URL",
}

SUPPORTED_RUNTIMES = frozenset(_RUNTIME_ENV)


class RuntimeConfigError(ValueError):
    def __init__(self, runtime: str, error_type: str, message: str):
        super().__init__(message)
        self.runtime = runtime
        self.error_type = error_type


def validate_http_url(value: object) -> bool:
    """Return True only for an absolute HTTP(S) URL with a hostname."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value.strip())
        # Accessing port also rejects malformed/non-numeric port declarations.
        parsed.port
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def resolve_runtime_url(runtime_name: str) -> str:
    """Resolve and validate a configured runtime URL."""
    env_key = _RUNTIME_ENV.get(runtime_name)
    if not env_key:
        raise RuntimeConfigError(
            runtime_name,
            "unsupported_runtime",
            f"Unsupported runtime '{runtime_name}'",
        )
    value = os.getenv(env_key)
    if not value:
        raise RuntimeConfigError(
            runtime_name,
            "runtime_url_missing",
            f"Runtime URL is not configured for '{runtime_name}'",
        )
    if not validate_http_url(value):
        raise RuntimeConfigError(
            runtime_name,
            "runtime_url_invalid",
            f"Runtime URL for '{runtime_name}' must be an absolute HTTP(S) URL",
        )
    return value.strip().rstrip("/")


def list_runtimes() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in _RUNTIME_ENV:
        try:
            result[name] = resolve_runtime_url(name)
        except RuntimeConfigError:
            result[name] = None
    return result
