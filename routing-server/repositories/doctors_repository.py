from datetime import datetime

from bson import ObjectId
from pymongo import ReturnDocument


class DoctorsRepository:
    def __init__(self, database):
        self.collection = database["doctors"]

    async def create(self, document: dict) -> dict:
        result = await self.collection.insert_one(document)
        created = dict(document)
        created["_id"] = result.inserted_id
        return created

    async def get_by_employee_id(self, employee_id: str) -> dict | None:
        return await self.collection.find_one({"employee_id": employee_id})

    async def get_by_id(self, doctor_id: ObjectId) -> dict | None:
        return await self.collection.find_one({"_id": doctor_id})

    async def update_last_login(self, doctor_id: ObjectId, at: datetime) -> None:
        await self.collection.update_one(
            {"_id": doctor_id},
            {"$set": {"last_login_at": at, "updated_at": at}},
        )

    async def update_password_hash(
        self,
        doctor_id: ObjectId,
        password_hash: str,
        at: datetime,
    ) -> None:
        await self.collection.update_one(
            {"_id": doctor_id},
            {"$set": {"password_hash": password_hash, "updated_at": at}},
        )

    async def upsert_seed(self, employee_id: str, values: dict) -> dict:
        return await self.collection.find_one_and_update(
            {"employee_id": employee_id},
            {
                "$set": values,
                "$setOnInsert": {"created_at": values["updated_at"]},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
