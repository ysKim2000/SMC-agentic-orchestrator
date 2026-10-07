from datetime import datetime

from bson import ObjectId


class SessionsRepository:
    def __init__(self, database):
        self.collection = database["auth_sessions"]

    async def create(self, document: dict) -> dict:
        result = await self.collection.insert_one(document)
        created = dict(document)
        created["_id"] = result.inserted_id
        return created

    async def get_active(self, session_id: str, now: datetime) -> dict | None:
        return await self.collection.find_one({
            "session_id": session_id,
            "revoked_at": None,
            "expires_at": {"$gt": now},
        })

    async def rotate(
        self,
        session_id: str,
        current_hash: str,
        next_hash: str,
        at: datetime,
    ) -> bool:
        result = await self.collection.update_one(
            {
                "session_id": session_id,
                "refresh_token_hash": current_hash,
                "revoked_at": None,
                "expires_at": {"$gt": at},
            },
            {
                "$set": {
                    "refresh_token_hash": next_hash,
                    "last_used_at": at,
                }
            },
        )
        return result.modified_count == 1

    async def revoke(
        self,
        session_id: str,
        refresh_token_hash: str,
        doctor_id: ObjectId,
        at: datetime,
    ) -> bool:
        result = await self.collection.update_one(
            {
                "session_id": session_id,
                "doctor_id": doctor_id,
                "refresh_token_hash": refresh_token_hash,
                "revoked_at": None,
            },
            {"$set": {"revoked_at": at}},
        )
        return result.modified_count == 1

    async def revoke_by_session_id(self, session_id: str, at: datetime) -> None:
        await self.collection.update_one(
            {"session_id": session_id, "revoked_at": None},
            {"$set": {"revoked_at": at}},
        )
