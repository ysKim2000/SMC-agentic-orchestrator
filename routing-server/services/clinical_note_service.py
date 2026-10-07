import uuid
from datetime import datetime, timezone

from services.clinical_service import ClinicalServiceError


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


class ClinicalNoteService:
    def __init__(self, database):
        self.notes = database["notes"]
        self.patients = database["patients"]
        self.visits = database["visits"]
        self.doctors = database["doctors"]

    async def _patient(self, patient_id: str, doctor: dict) -> dict:
        patient = await self.patients.find_one({
            "patient_id": patient_id,
            "hospital_id": doctor["hospital_id"],
            "is_archived": False,
        })
        if not patient:
            raise ClinicalServiceError(404, "환자를 찾을 수 없습니다.")
        return patient

    async def _response(self, note: dict, doctors: dict | None = None) -> dict:
        author_id = note.get("author_id") or note.get("doctor_id")
        author = (doctors or {}).get(author_id)
        if author is None and author_id is not None:
            author = await self.doctors.find_one({"_id": author_id})
        return {
            "note_id": note.get("note_id") or str(note["_id"]),
            "patient_id": note["patient_id"],
            "visit_id": note.get("visit_id"),
            "author_id": str(author_id),
            "author_name": (
                note.get("author_name")
                or (author or {}).get("name")
                or "알 수 없는 작성자"
            ),
            "source": note.get("source") or "registration",
            "text": note.get("text") or note.get("content") or "",
            "created_at": _iso(note["created_at"]),
        }

    async def list_notes(self, patient_id: str, doctor: dict) -> list[dict]:
        await self._patient(patient_id, doctor)
        notes = await self.notes.find({
            "patient_id": patient_id,
            "hospital_id": doctor["hospital_id"],
        }).sort("created_at", 1).to_list(None)
        author_ids = {
            note.get("author_id") or note.get("doctor_id")
            for note in notes
            if note.get("author_id") or note.get("doctor_id")
        }
        doctors = {
            item["_id"]: item
            async for item in self.doctors.find({"_id": {"$in": list(author_ids)}})
        }
        return [await self._response(note, doctors) for note in notes]

    async def create_note(
        self,
        patient_id: str,
        text: str,
        visit_id: str | None,
        doctor: dict,
    ) -> dict:
        await self._patient(patient_id, doctor)
        if visit_id is not None:
            visit = await self.visits.find_one({
                "visit_id": visit_id,
                "patient_id": patient_id,
                "hospital_id": doctor["hospital_id"],
            })
            if not visit:
                raise ClinicalServiceError(
                    422,
                    "visit_id가 해당 환자의 방문이 아닙니다.",
                )
        now = datetime.now(timezone.utc)
        note = {
            "note_id": str(uuid.uuid4()),
            "hospital_id": doctor["hospital_id"],
            "patient_id": patient_id,
            "visit_id": visit_id,
            "author_id": doctor["_id"],
            "author_name": doctor["name"],
            "source": "clinical",
            "text": text,
            "created_at": now,
            "updated_at": now,
        }
        result = await self.notes.insert_one(note)
        note["_id"] = result.inserted_id
        return await self._response(note, {doctor["_id"]: doctor})
