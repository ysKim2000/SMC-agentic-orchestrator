from datetime import datetime

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class AnalysisQueueRepository:
    def __init__(self, database):
        self.db = database
        self.analyses = database["analyses"]
        self.appointments = database["appointments"]
        self.locks = database["doctor_analysis_locks"]

    async def queued_doctors(self, limit: int) -> list[ObjectId]:
        pipeline = [
            {"$match": {"status": "queued"}},
            {"$group": {
                "_id": "$requested_by_doctor_id",
                "first_scheduled_at": {"$min": "$scheduled_at"},
            }},
            {"$sort": {"first_scheduled_at": 1}},
            {"$limit": limit},
        ]
        return [item["_id"] async for item in self.analyses.aggregate(pipeline)]

    async def acquire_lock(
        self,
        doctor_id: ObjectId,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        try:
            lock = await self.locks.find_one_and_update(
                {
                    "_id": doctor_id,
                    "$or": [
                        {"lease_expires_at": {"$lte": now}},
                        {"owner_worker_id": worker_id},
                    ],
                },
                {"$set": {
                    "owner_worker_id": worker_id,
                    "analysis_id": None,
                    "lease_expires_at": lease_expires_at,
                    "updated_at": now,
                }},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return bool(lock and lock.get("owner_worker_id") == worker_id)

    async def claim_next(
        self,
        doctor_id: ObjectId,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> dict | None:
        analysis = await self.analyses.find_one_and_update(
            {
                "requested_by_doctor_id": doctor_id,
                "status": "queued",
            },
            {
                "$set": {
                    "status": "analyzing",
                    "worker_id": worker_id,
                    "started_at": now,
                    "lease_expires_at": lease_expires_at,
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
            },
            sort=[("scheduled_at", 1), ("queued_at", 1), ("_id", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if analysis:
            await self.locks.update_one(
                {"_id": doctor_id, "owner_worker_id": worker_id},
                {"$set": {
                    "analysis_id": analysis["analysis_id"],
                    "lease_expires_at": lease_expires_at,
                    "updated_at": now,
                }},
            )
        return analysis

    async def appointment_is_active(self, appointment_id: str) -> bool:
        appointment = await self.appointments.find_one(
            {"appointment_id": appointment_id},
            {"status": 1},
        )
        return bool(appointment and appointment.get("status") != "cancelled")

    async def cancel_claim(self, analysis_id: str, worker_id: str, now: datetime) -> None:
        await self.analyses.update_one(
            {
                "analysis_id": analysis_id,
                "status": "analyzing",
                "worker_id": worker_id,
            },
            {"$set": {
                "status": "cancelled",
                "risk_status": "unavailable",
                "completed_at": now,
                "lease_expires_at": None,
                "updated_at": now,
            }},
        )

    async def extend_lease(
        self,
        analysis: dict,
        worker_id: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> bool:
        result = await self.analyses.update_one(
            {
                "_id": analysis["_id"],
                "status": "analyzing",
                "worker_id": worker_id,
            },
            {"$set": {"lease_expires_at": lease_expires_at, "updated_at": now}},
        )
        await self.locks.update_one(
            {
                "_id": analysis["requested_by_doctor_id"],
                "owner_worker_id": worker_id,
            },
            {"$set": {"lease_expires_at": lease_expires_at, "updated_at": now}},
        )
        return result.modified_count == 1

    async def mark_done(
        self,
        analysis_id: str,
        worker_id: str,
        result: dict,
        now: datetime,
    ) -> bool:
        update = {
            **result,
            "status": "done",
            "completed_at": now,
            "lease_expires_at": None,
            "updated_at": now,
            "error": None,
        }
        outcome = await self.analyses.update_one(
            {
                "analysis_id": analysis_id,
                "status": "analyzing",
                "worker_id": worker_id,
            },
            {"$set": update},
        )
        return outcome.modified_count == 1

    async def mark_failed(
        self,
        analysis_id: str,
        worker_id: str,
        error: str,
        now: datetime,
    ) -> bool:
        outcome = await self.analyses.update_one(
            {
                "analysis_id": analysis_id,
                "status": "analyzing",
                "worker_id": worker_id,
            },
            {"$set": {
                "status": "failed",
                "risk_tier": None,
                "risk_status": "unavailable",
                "completed_at": now,
                "lease_expires_at": None,
                "updated_at": now,
                "error": error[:2000],
            }},
        )
        return outcome.modified_count == 1

    async def handle_execution_failure(
        self,
        analysis: dict,
        worker_id: str,
        error: str,
        max_attempts: int,
        now: datetime,
    ) -> str:
        if analysis.get("attempt", 0) < max_attempts:
            outcome = await self.analyses.update_one(
                {
                    "_id": analysis["_id"],
                    "status": "analyzing",
                    "worker_id": worker_id,
                },
                {"$set": {
                    "status": "queued",
                    "worker_id": None,
                    "started_at": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                    "error": error[:2000],
                }},
            )
            return "queued" if outcome.modified_count == 1 else "lost"
        saved = await self.mark_failed(
            analysis["analysis_id"], worker_id, error, now
        )
        return "failed" if saved else "lost"

    async def release_lock(self, doctor_id: ObjectId, worker_id: str) -> None:
        await self.locks.delete_one({
            "_id": doctor_id,
            "owner_worker_id": worker_id,
        })

    async def recover_expired(
        self,
        now: datetime,
        max_attempts: int,
    ) -> tuple[int, int]:
        retry = await self.analyses.update_many(
            {
                "status": "analyzing",
                "lease_expires_at": {"$lte": now},
                "attempt": {"$lt": max_attempts},
            },
            {"$set": {
                "status": "queued",
                "worker_id": None,
                "started_at": None,
                "lease_expires_at": None,
                "updated_at": now,
                "error": "worker lease expired; retry scheduled",
            }},
        )
        failed = await self.analyses.update_many(
            {
                "status": "analyzing",
                "lease_expires_at": {"$lte": now},
                "attempt": {"$gte": max_attempts},
            },
            {"$set": {
                "status": "failed",
                "worker_id": None,
                "lease_expires_at": None,
                "completed_at": now,
                "updated_at": now,
                "risk_tier": None,
                "risk_status": "unavailable",
                "error": "worker lease expired; maximum attempts exceeded",
            }},
        )
        return retry.modified_count, failed.modified_count
