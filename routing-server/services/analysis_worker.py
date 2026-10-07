import asyncio
import logging
import socket
import uuid
from datetime import datetime, timedelta, timezone

from config.settings import (
    ANALYSIS_GLOBAL_CONCURRENCY,
    ANALYSIS_LEASE_SECONDS,
    ANALYSIS_MAX_ATTEMPTS,
    ANALYSIS_POLL_INTERVAL_SECONDS,
    ANALYSIS_WORKER_ID,
)
from repositories.analysis_queue_repository import AnalysisQueueRepository
from services.clinical_inference_service import ClinicalInferenceService


logger = logging.getLogger("maple.analysis-worker")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisWorker:
    def __init__(self, database, executor=None, worker_id: str | None = None):
        self.repository = AnalysisQueueRepository(database)
        self.executor = executor or ClinicalInferenceService(database)
        self.worker_id = worker_id or ANALYSIS_WORKER_ID or (
            f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        )
        self._stopping = asyncio.Event()
        self._active_doctors = set()

    async def _heartbeat(self, analysis: dict) -> None:
        interval = max(1.0, ANALYSIS_LEASE_SECONDS / 3)
        while not self._stopping.is_set():
            await asyncio.sleep(interval)
            now = _utcnow()
            extended = await self.repository.extend_lease(
                analysis,
                self.worker_id,
                now,
                now + timedelta(seconds=ANALYSIS_LEASE_SECONDS),
            )
            if not extended:
                return

    async def _process_doctor(self, doctor_id) -> bool:
        now = _utcnow()
        acquired = await self.repository.acquire_lock(
            doctor_id,
            self.worker_id,
            now,
            now + timedelta(seconds=ANALYSIS_LEASE_SECONDS),
        )
        if not acquired:
            return False

        analysis = None
        heartbeat = None
        try:
            analysis = await self.repository.claim_next(
                doctor_id,
                self.worker_id,
                now,
                now + timedelta(seconds=ANALYSIS_LEASE_SECONDS),
            )
            if not analysis:
                return False
            if not await self.repository.appointment_is_active(
                analysis["appointment_id"]
            ):
                await self.repository.cancel_claim(
                    analysis["analysis_id"], self.worker_id, _utcnow()
                )
                return True

            logger.info(
                "분석 시작 analysis_id=%s doctor_id=%s attempt=%s",
                analysis["analysis_id"],
                doctor_id,
                analysis["attempt"],
            )
            heartbeat = asyncio.create_task(self._heartbeat(analysis))
            try:
                result = await self.executor.execute(analysis)
                saved = await self.repository.mark_done(
                    analysis["analysis_id"], self.worker_id, result, _utcnow()
                )
                if not saved:
                    logger.warning("분석 완료 저장 시 lease 소유권을 잃었습니다: %s", analysis["analysis_id"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("분석 실패 analysis_id=%s", analysis["analysis_id"])
                next_status = await self.repository.handle_execution_failure(
                    analysis,
                    self.worker_id,
                    str(exc),
                    ANALYSIS_MAX_ATTEMPTS,
                    _utcnow(),
                )
                logger.warning(
                    "분석 실패 처리 analysis_id=%s next_status=%s",
                    analysis["analysis_id"],
                    next_status,
                )
            return True
        finally:
            if heartbeat:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            await self.repository.release_lock(doctor_id, self.worker_id)

    async def run_once(self) -> int:
        now = _utcnow()
        retried, failed = await self.repository.recover_expired(
            now, ANALYSIS_MAX_ATTEMPTS
        )
        if retried or failed:
            logger.warning("만료 분석 복구 retry=%s failed=%s", retried, failed)

        doctor_ids = await self.repository.queued_doctors(
            ANALYSIS_GLOBAL_CONCURRENCY * 2
        )
        selected = [
            doctor_id
            for doctor_id in doctor_ids
            if doctor_id not in self._active_doctors
        ][:ANALYSIS_GLOBAL_CONCURRENCY]
        if not selected:
            return 0

        self._active_doctors.update(selected)
        try:
            results = await asyncio.gather(
                *(self._process_doctor(doctor_id) for doctor_id in selected),
                return_exceptions=True,
            )
            processed = 0
            for result in results:
                if isinstance(result, Exception):
                    logger.error(
                        "의사 큐 처리 중 예외",
                        exc_info=(type(result), result, result.__traceback__),
                    )
                else:
                    processed += bool(result)
            return processed
        finally:
            self._active_doctors.difference_update(selected)

    async def run_forever(self) -> None:
        logger.info(
            "분석 worker 시작 id=%s global_concurrency=%s",
            self.worker_id,
            ANALYSIS_GLOBAL_CONCURRENCY,
        )
        try:
            while not self._stopping.is_set():
                try:
                    processed = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("분석 worker loop 오류; 다음 polling에서 재시도합니다.")
                    processed = 0
                if not processed:
                    try:
                        await asyncio.wait_for(
                            self._stopping.wait(),
                            timeout=ANALYSIS_POLL_INTERVAL_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        pass
        finally:
            logger.info("분석 worker 종료 id=%s", self.worker_id)

    def stop(self) -> None:
        self._stopping.set()
