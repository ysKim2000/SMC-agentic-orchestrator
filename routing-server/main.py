import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.database import close_database, connect_database, ensure_indexes, get_database
from config.settings import (
    ACCESS_TOKEN_MINUTES,
    ANALYSIS_WORKER_ENABLED,
    AGENT_URL,
    CLIENT_ORIGINS,
    DB_NAME,
    INFERENCE_HEALTH_PATH,
    INFERENCE_URL,
    JWT_SECRET,
    REQUIRE_REPLICA_SET,
)
from services.analysis_worker import AnalysisWorker
from config.logging_filters import install_signed_url_redaction

# ── 로깅 설정 ──────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
install_signed_url_redaction()
logger = logging.getLogger("maple")


async def log_http_service_status(
    service_name: str,
    base_url: str,
    health_path: str = "/health",
    timeout: float = 3.0,
) -> None:
    """Log whether an external HTTP service is reachable without blocking startup."""
    base_url = base_url.rstrip("/")
    health_url = f"{base_url}{health_path}"
    logger.info(f"{service_name} 연결 확인 중... ({base_url})")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(health_url)
            response.raise_for_status()
        logger.info(f"{service_name} 연결 완료 ({health_url})")
    except Exception as exc:
        logger.warning(f"{service_name} 연결 확인 실패 ({health_url}): {exc}")


# ── Lifespan ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    db_name               = DB_NAME
    agent_url             = AGENT_URL
    inference_url         = INFERENCE_URL
    inference_health_path = INFERENCE_HEALTH_PATH

    logger.info("서버 시작 중...")
    if JWT_SECRET == "dev-only-change-me" and REQUIRE_REPLICA_SET:
        raise RuntimeError(
            "JWT_SECRET must be configured when REQUIRE_REPLICA_SET=true"
        )

    logger.info("MongoDB 연결 중... (DB: %s)", db_name)
    await connect_database()
    worker = None
    worker_task = None
    try:
        await ensure_indexes()
        app.mongodb = get_database()
        logger.info(f"MongoDB 연결 완료 (DB: {db_name})")
        if JWT_SECRET in {
            "dev-only-change-me",
            "maple-local-development-secret-change-before-production",
        }:
            logger.warning(
                "개발용 JWT_SECRET을 사용 중입니다. 실제 환자 데이터 연결 전 반드시 변경하세요."
            )
        logger.info("인증 access token 수명: %s분", ACCESS_TOKEN_MINUTES)
        logger.info(f"AI Agent URL: {agent_url}")
        await log_http_service_status(
            "추론 서버",
            inference_url,
            health_path=inference_health_path,
        )
        if ANALYSIS_WORKER_ENABLED:
            worker = AnalysisWorker(get_database())
            worker_task = asyncio.create_task(
                worker.run_forever(),
                name="maple-analysis-worker",
            )
            app.state.analysis_worker = worker
        logger.info("Maple AI Backend 서버가 준비되었습니다.")
        yield
    finally:
        logger.info("서버 종료 중...")
        if worker:
            worker.stop()
        if worker_task:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        await close_database()
        logger.info("MongoDB 연결 종료")


# ── FastAPI 앱 ─────────────────────────────────────
app = FastAPI(
    title="Maple AI Backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CLIENT_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["Content-Length", "Content-Type", "ETag"],
    max_age=600,
)

# ── 헬스체크 ───────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "maple-ai-backend"}


# ── 라우터 등록 ────────────────────────────────────
# routes/api.py 에서 /projects, /inference, /admin, /pipeline 을 한 번에 등록
from routes.api import router as api_router
app.include_router(api_router)
logger.info(
    "라우터 등록 완료 "
    "(/auth, /patients, /visits, /visits/{id}/chat, /analyses, /files, "
    "/appointments, /projects, /inference, /admin, /pipeline)"
)

# uvicorn main:app --host 0.0.0.0 --port 8100 --reload
# http://127.0.0.1:8100/docs
