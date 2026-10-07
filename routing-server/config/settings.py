import os
from dotenv import load_dotenv

load_dotenv()


def _csv_env(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# ── MongoDB ───────────────────────────────────────────
MONGO_URI = os.getenv("MONGO_URI") or os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.getenv("DB_NAME", "maple_db")
REQUIRE_REPLICA_SET = _bool_env("REQUIRE_REPLICA_SET", False)

# ── 인증 ──────────────────────────────────────────────
JWT_SECRET = os.getenv("JWT_SECRET", "dev-only-change-me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "30"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "7"))

# ── 단일 병원 ─────────────────────────────────────────
HOSPITAL_ID = os.getenv("HOSPITAL_ID", "champion")
HOSPITAL_NAME = os.getenv("HOSPITAL_NAME", "챔피언 병원")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Seoul")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
GRIDFS_BUCKET = os.getenv("GRIDFS_BUCKET", "fs")
CLINICAL_WORK_DIR = os.getenv("CLINICAL_WORK_DIR", "./data/clinical-work")
FILE_SIGNING_SECRET = os.getenv("FILE_SIGNING_SECRET") or JWT_SECRET
FILE_SIGNED_URL_SECONDS = int(os.getenv("FILE_SIGNED_URL_SECONDS", "300"))
AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))

# ── 분석 worker ─────────────────────────────────────
ANALYSIS_WORKER_ENABLED = _bool_env("ANALYSIS_WORKER_ENABLED", True)
ANALYSIS_WORKER_ID = os.getenv("ANALYSIS_WORKER_ID", "")
ANALYSIS_GLOBAL_CONCURRENCY = int(os.getenv("ANALYSIS_GLOBAL_CONCURRENCY", "3"))
ANALYSIS_LEASE_SECONDS = int(os.getenv("ANALYSIS_LEASE_SECONDS", "300"))
ANALYSIS_MAX_ATTEMPTS = int(os.getenv("ANALYSIS_MAX_ATTEMPTS", "2"))
ANALYSIS_POLL_INTERVAL_SECONDS = float(
    os.getenv("ANALYSIS_POLL_INTERVAL_SECONDS", "2")
)

# ── 임상 채팅 ────────────────────────────────────────
CHAT_MAX_HISTORY_MESSAGES = int(os.getenv("CHAT_MAX_HISTORY_MESSAGES", "40"))
CHAT_MAX_CONTEXT_ANALYSES = int(os.getenv("CHAT_MAX_CONTEXT_ANALYSES", "20"))
CHAT_MAX_CONTEXT_CHARS = int(os.getenv("CHAT_MAX_CONTEXT_CHARS", "24000"))

# ── 클라이언트 ────────────────────────────────────────
CLIENT_ORIGINS = _csv_env(
    "CLIENT_ORIGINS",
    "http://localhost:3000,app://maple",
)

# ── AI Agent ──────────────────────────────────────────
AGENT_URL     = os.getenv("AGENT_URL", "http://localhost:8101")
AGENT_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "300"))
# general 모드에서 Agent(VLM)로 보내는 요청당 최대 이미지 수 (토큰 예산).
# 초과 시 3D 첨부를 axial 단면으로 강등 → 그래도 초과면 하드 캡. (§4)
AGENT_MAX_IMAGES = int(os.getenv("AGENT_MAX_IMAGES", "8"))

# ── 추론 서버 ─────────────────────────────────────────
# nnU-Net segmentation on a full-resolution chest CT runs past two minutes on the
# runtime container (measured: consolidation 156 s, airway/vessel 160 s), so the old
# 130 s client default cut those two off mid-inference while the container kept working.
INFERENCE_TIMEOUT     = float(os.getenv("MAPLE_INFERENCE_TIMEOUT", "600"))
INFERENCE_URL         = os.getenv("MAPLE_INFERENCE_URL", "http://localhost:8110")
INFERENCE_HEALTH_PATH = os.getenv("MAPLE_INFERENCE_HEALTH_PATH", "/health")
REMOTE_INFERENCE_URL  = os.getenv("MAPLE_REMOTE_INFERENCE_URL", "")

# ── 모델 경로 ─────────────────────────────────────────
AI_MODELS_DIR = os.getenv("AI_MODELS_DIR", "../model-execution-server/AI_Models")
MODEL_ROOT    = os.getenv("MODEL_ROOT", "")

# ── MongoDB 문서 ID ───────────────────────────────────
MAIN_DOCUMENT_ID = os.getenv("MAIN_DOCUMENT_ID", "")
