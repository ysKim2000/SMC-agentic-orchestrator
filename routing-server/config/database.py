import logging

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING, IndexModel

from config.settings import DB_NAME
from config.settings import MAIN_DOCUMENT_ID as MAIN_DOCUMENT_ID_RAW
from config.settings import AUDIT_RETENTION_DAYS, MONGO_URI, REQUIRE_REPLICA_SET

logger = logging.getLogger("maple.database")

_client: AsyncIOMotorClient | None = None
_database = None


def _parse_object_id(value: str) -> ObjectId | None:
    if not value:
        return None
    if not ObjectId.is_valid(value):
        raise RuntimeError("MAIN_DOCUMENT_ID must be a valid MongoDB ObjectId")
    return ObjectId(value)


MAIN_DOCUMENT_ID = _parse_object_id(MAIN_DOCUMENT_ID_RAW)


async def connect_database() -> None:
    global _client, _database
    if _client is not None:
        return

    _client = AsyncIOMotorClient(MONGO_URI)
    _database = _client[DB_NAME]
    try:
        await _client.admin.command("ping")
        hello = await _client.admin.command("hello")
    except Exception:
        await close_database()
        raise

    replica_set_name = hello.get("setName")
    if REQUIRE_REPLICA_SET and not replica_set_name:
        await close_database()
        raise RuntimeError(
            "MongoDB replica set is required. Start MongoDB with --replSet "
            "and configure MONGO_URI with ?replicaSet=rs0."
        )
    if replica_set_name:
        logger.info("MongoDB replica set 확인: %s", replica_set_name)
    else:
        logger.warning(
            "MongoDB가 standalone으로 실행 중입니다. "
            "환자 접수 트랜잭션 구현 전 single-node replica set 전환이 필요합니다."
        )


async def close_database() -> None:
    global _client, _database
    if _client is not None:
        _client.close()
    _client = None
    _database = None


def get_database():
    if _database is None:
        raise RuntimeError("MongoDB is not connected. Application lifespan has not started.")
    return _database


async def ensure_indexes() -> None:
    db = get_database()

    await db["doctors"].create_indexes([
        IndexModel([("employee_id", ASCENDING)], unique=True, name="uq_doctors_employee_id"),
        IndexModel(
            [("hospital_id", ASCENDING), ("is_active", ASCENDING)],
            name="ix_doctors_hospital_active",
        ),
    ])
    await db["auth_sessions"].create_indexes([
        IndexModel([("session_id", ASCENDING)], unique=True, name="uq_auth_sessions_session_id"),
        IndexModel(
            [("doctor_id", ASCENDING), ("revoked_at", ASCENDING)],
            name="ix_auth_sessions_doctor_revoked",
        ),
        IndexModel(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            name="ttl_auth_sessions_expires_at",
        ),
    ])
    await db["patients"].create_indexes([
        IndexModel([("patient_id", ASCENDING)], unique=True, name="uq_patients_patient_id"),
        IndexModel(
            [("hospital_id", ASCENDING), ("search_name", ASCENDING)],
            name="ix_patients_hospital_search_name",
        ),
        IndexModel(
            [("hospital_id", ASCENDING), ("assigned_doctor_id", ASCENDING)],
            name="ix_patients_hospital_doctor",
        ),
        IndexModel(
            [("hospital_id", ASCENDING), ("is_archived", ASCENDING)],
            name="ix_patients_hospital_archived",
        ),
    ])
    await db["appointments"].create_indexes([
        IndexModel([("appointment_id", ASCENDING)], unique=True, name="uq_appointments_id"),
        IndexModel(
            [
                ("hospital_id", ASCENDING),
                ("scheduled_date", ASCENDING),
                ("scheduled_time", ASCENDING),
            ],
            name="ix_appointments_schedule",
        ),
        IndexModel(
            [("hospital_id", ASCENDING), ("doctor_id", ASCENDING), ("scheduled_at", ASCENDING)],
            unique=True,
            partialFilterExpression={"slot_claimed": True},
            name="uq_appointments_active_doctor_slot",
        ),
        IndexModel(
            [("patient_id", ASCENDING), ("scheduled_at", DESCENDING)],
            name="ix_appointments_patient_history",
        ),
    ])
    await db["visits"].create_indexes([
        IndexModel([("visit_id", ASCENDING)], unique=True, name="uq_visits_id"),
        IndexModel(
            [("patient_id", ASCENDING), ("visit_date", DESCENDING), ("created_at", DESCENDING)],
            name="ix_visits_patient_history",
        ),
        IndexModel([("appointment_id", ASCENDING)], name="ix_visits_appointment"),
    ])
    await db["analyses"].create_indexes([
        IndexModel([("analysis_id", ASCENDING)], unique=True, name="uq_analyses_id"),
        IndexModel(
            [
                ("requested_by_doctor_id", ASCENDING),
                ("status", ASCENDING),
                ("scheduled_at", ASCENDING),
                ("queued_at", ASCENDING),
            ],
            name="ix_analyses_doctor_queue",
        ),
        IndexModel(
            [("patient_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_analyses_patient_history",
        ),
        IndexModel(
            [("visit_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_analyses_visit_history",
        ),
        IndexModel(
            [("status", ASCENDING), ("lease_expires_at", ASCENDING)],
            name="ix_analyses_lease_recovery",
        ),
    ])
    await db["doctor_analysis_locks"].create_indexes([
        IndexModel(
            [("lease_expires_at", ASCENDING)],
            name="ix_doctor_analysis_locks_lease",
        ),
    ])
    await db["notes"].create_indexes([
        IndexModel([("note_id", ASCENDING)], unique=True, sparse=True, name="uq_notes_id"),
        IndexModel(
            [("patient_id", ASCENDING), ("created_at", ASCENDING)],
            name="ix_notes_patient_created",
        ),
        IndexModel(
            [("visit_id", ASCENDING), ("created_at", ASCENDING)],
            name="ix_notes_visit_created",
        ),
    ])
    await db["chat_messages"].create_indexes([
        IndexModel([("message_id", ASCENDING)], unique=True, name="uq_chat_messages_id"),
        IndexModel(
            [("patient_id", ASCENDING), ("created_at", ASCENDING)],
            name="ix_chat_patient_created",
        ),
        IndexModel(
            [("visit_id", ASCENDING), ("created_at", ASCENDING)],
            name="ix_chat_visit_created",
        ),
    ])
    await db["chat_response_fixtures"].create_indexes([
        IndexModel(
            [
                ("hospital_id", ASCENDING),
                ("patient_id", ASCENDING),
                ("doctor_employee_id", ASCENDING),
                ("active", ASCENDING),
            ],
            name="ix_chat_fixtures_scope",
        ),
    ])
    await db["chat_response_fixture_states"].create_indexes([
        IndexModel(
            [("doctor_id", ASCENDING), ("fixture_key", ASCENDING)],
            name="ix_chat_fixture_states_doctor",
        ),
    ])
    await db["medical_files"].create_indexes([
        IndexModel([("file_id", ASCENDING)], unique=True, name="uq_medical_files_id"),
        IndexModel([("gridfs_id", ASCENDING)], unique=True, name="uq_medical_files_gridfs_id"),
        IndexModel(
            [("analysis_id", ASCENDING), ("role", ASCENDING), ("slice_index", ASCENDING)],
            name="ix_medical_files_analysis_role_slice",
        ),
        IndexModel(
            [("visit_id", ASCENDING), ("created_at", ASCENDING)],
            name="ix_medical_files_visit_created",
        ),
        IndexModel([("sha256", ASCENDING)], name="ix_medical_files_sha256"),
    ])
    await db["access_logs"].create_indexes([
        IndexModel(
            [("doctor_id", ASCENDING), ("at", DESCENDING)],
            name="ix_access_logs_doctor_at",
        ),
        IndexModel(
            [("resource_id", ASCENDING), ("at", DESCENDING)],
            name="ix_access_logs_resource_at",
        ),
        IndexModel(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            name="ttl_access_logs_expires_at",
        ),
    ])

    logger.info("MongoDB 임상 컬렉션 인덱스 확인 완료")
