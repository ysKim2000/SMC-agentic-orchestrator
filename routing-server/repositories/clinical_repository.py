from datetime import datetime

from bson import ObjectId
from pymongo import ReturnDocument


class ClinicalRepository:
    def __init__(self, database):
        self.db = database
        self.counters = database["counters"]
        self.patients = database["patients"]
        self.appointments = database["appointments"]
        self.visits = database["visits"]
        self.analyses = database["analyses"]
        self.notes = database["notes"]
        self.medical_files = database["medical_files"]
        self.doctors = database["doctors"]

    async def next_sequence(
        self,
        key: str,
        *,
        session=None,
        initial_value: int = 0,
    ) -> int:
        document = await self.counters.find_one_and_update(
            {"_id": key},
            [{
                "$set": {
                    "seq": {"$add": [{"$ifNull": ["$seq", initial_value]}, 1]},
                    "updated_at": "$$NOW",
                }
            }],
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return document["seq"]

    async def get_patient(self, patient_id: str, hospital_id: str, *, session=None):
        return await self.patients.find_one(
            {
                "patient_id": patient_id,
                "hospital_id": hospital_id,
                "is_archived": False,
            },
            session=session,
        )

    async def get_appointment(
        self,
        appointment_id: str,
        hospital_id: str,
        *,
        session=None,
    ):
        return await self.appointments.find_one(
            {"appointment_id": appointment_id, "hospital_id": hospital_id},
            session=session,
        )

    async def get_doctors(self, doctor_ids: set[ObjectId]) -> dict[ObjectId, dict]:
        if not doctor_ids:
            return {}
        cursor = self.doctors.find({"_id": {"$in": list(doctor_ids)}})
        return {doctor["_id"]: doctor async for doctor in cursor}
