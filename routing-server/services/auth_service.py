import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from bson import ObjectId
from jose import ExpiredSignatureError, JWTError, jwt
from pymongo.errors import DuplicateKeyError

from config.settings import (
    ACCESS_TOKEN_MINUTES,
    HOSPITAL_ID,
    HOSPITAL_NAME,
    JWT_ALGORITHM,
    JWT_SECRET,
    REFRESH_TOKEN_DAYS,
)
from models.auth_schemas import DoctorResponse, LoginRequest, SignupRequest, TokenResponse
from repositories.doctors_repository import DoctorsRepository
from repositories.sessions_repository import SessionsRepository


@dataclass
class AuthServiceError(Exception):
    status_code: int
    detail: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuthService:
    def __init__(
        self,
        doctors: DoctorsRepository,
        sessions: SessionsRepository,
        password_hasher: PasswordHasher | None = None,
    ):
        self.doctors = doctors
        self.sessions = sessions
        self.password_hasher = password_hasher or PasswordHasher()

    @staticmethod
    def _doctor_response(doctor: dict) -> DoctorResponse:
        name = doctor.get("name", "")
        return DoctorResponse(
            employee_id=doctor["employee_id"],
            name=name,
            hospital=doctor.get("hospital", ""),
            department=doctor.get("department", ""),
            title=doctor.get("title", ""),
            initial=doctor.get("initial") or name[:1],
            roles=list(doctor.get("roles") or ["doctor"]),
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_secret() -> None:
        if not JWT_SECRET:
            raise AuthServiceError(503, "authentication is not configured")

    def _encode_token(
        self,
        *,
        doctor: dict,
        session_id: str,
        token_type: str,
        expires_at: datetime,
    ) -> str:
        self._validate_secret()
        now = _utcnow()
        payload = {
            "sub": str(doctor["_id"]),
            "employee_id": doctor["employee_id"],
            "hospital_id": doctor["hospital_id"],
            "roles": list(doctor.get("roles") or ["doctor"]),
            "sid": session_id,
            "type": token_type,
            "jti": str(uuid.uuid4()),
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    def _decode_token(self, token: str, expected_type: str) -> dict:
        self._validate_secret()
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except ExpiredSignatureError as exc:
            raise AuthServiceError(401, "token expired") from exc
        except JWTError as exc:
            raise AuthServiceError(401, "invalid token") from exc

        if payload.get("type") != expected_type:
            raise AuthServiceError(401, "invalid token type")
        if not payload.get("sub") or not payload.get("sid"):
            raise AuthServiceError(401, "invalid token claims")
        if not ObjectId.is_valid(payload["sub"]):
            raise AuthServiceError(401, "invalid token subject")
        return payload

    async def _issue_session(
        self,
        doctor: dict,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> TokenResponse:
        now = _utcnow()
        access_expires_at = now + timedelta(minutes=ACCESS_TOKEN_MINUTES)
        refresh_expires_at = now + timedelta(days=REFRESH_TOKEN_DAYS)
        session_id = str(uuid.uuid4())

        access_token = self._encode_token(
            doctor=doctor,
            session_id=session_id,
            token_type="access",
            expires_at=access_expires_at,
        )
        refresh_token = self._encode_token(
            doctor=doctor,
            session_id=session_id,
            token_type="refresh",
            expires_at=refresh_expires_at,
        )
        await self.sessions.create({
            "session_id": session_id,
            "doctor_id": doctor["_id"],
            "refresh_token_hash": self._token_hash(refresh_token),
            "created_at": now,
            "expires_at": refresh_expires_at,
            "last_used_at": now,
            "revoked_at": None,
            "created_ip": ip,
            "user_agent": user_agent,
        })
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=ACCESS_TOKEN_MINUTES * 60,
            doctor=self._doctor_response(doctor),
        )

    async def signup(
        self,
        request: SignupRequest,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> TokenResponse:
        if request.hospital != HOSPITAL_NAME:
            raise AuthServiceError(422, "unsupported hospital")

        now = _utcnow()
        doctor = {
            "employee_id": request.employee_id,
            "password_hash": self.password_hasher.hash(request.password),
            "name": request.name,
            "hospital_id": HOSPITAL_ID,
            "hospital": HOSPITAL_NAME,
            "department": request.department,
            "title": request.title,
            "initial": request.name[:1],
            "roles": ["doctor"],
            "is_active": True,
            "created_at": now,
            "updated_at": now,
            "last_login_at": now,
        }
        try:
            doctor = await self.doctors.create(doctor)
        except DuplicateKeyError as exc:
            raise AuthServiceError(409, "employee_id already exists") from exc

        return await self._issue_session(doctor, ip=ip, user_agent=user_agent)

    async def login(
        self,
        request: LoginRequest,
        *,
        ip: str | None,
        user_agent: str | None,
    ) -> TokenResponse:
        doctor = await self.doctors.get_by_employee_id(request.employee_id)
        if not doctor or not doctor.get("is_active", False):
            raise AuthServiceError(401, "invalid credentials")

        try:
            self.password_hasher.verify(doctor["password_hash"], request.password)
        except (InvalidHashError, VerificationError, VerifyMismatchError, KeyError) as exc:
            raise AuthServiceError(401, "invalid credentials") from exc

        now = _utcnow()
        if self.password_hasher.check_needs_rehash(doctor["password_hash"]):
            next_hash = self.password_hasher.hash(request.password)
            await self.doctors.update_password_hash(doctor["_id"], next_hash, now)
            doctor["password_hash"] = next_hash

        await self.doctors.update_last_login(doctor["_id"], now)
        doctor["last_login_at"] = now
        return await self._issue_session(doctor, ip=ip, user_agent=user_agent)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        payload = self._decode_token(refresh_token, "refresh")
        now = _utcnow()
        doctor_id = ObjectId(payload["sub"])
        session_id = payload["sid"]
        session = await self.sessions.get_active(session_id, now)
        if not session or session.get("doctor_id") != doctor_id:
            raise AuthServiceError(401, "invalid refresh session")

        current_hash = self._token_hash(refresh_token)
        stored_hash = session.get("refresh_token_hash", "")
        if not hmac.compare_digest(stored_hash, current_hash):
            await self.sessions.revoke_by_session_id(session_id, now)
            raise AuthServiceError(401, "refresh token reuse detected")

        doctor = await self.doctors.get_by_id(doctor_id)
        if not doctor or not doctor.get("is_active", False):
            await self.sessions.revoke_by_session_id(session_id, now)
            raise AuthServiceError(401, "inactive account")

        access_expires_at = now + timedelta(minutes=ACCESS_TOKEN_MINUTES)
        refresh_expires_at = session["expires_at"]
        if refresh_expires_at.tzinfo is None:
            refresh_expires_at = refresh_expires_at.replace(tzinfo=timezone.utc)

        next_access_token = self._encode_token(
            doctor=doctor,
            session_id=session_id,
            token_type="access",
            expires_at=access_expires_at,
        )
        next_refresh_token = self._encode_token(
            doctor=doctor,
            session_id=session_id,
            token_type="refresh",
            expires_at=refresh_expires_at,
        )
        rotated = await self.sessions.rotate(
            session_id,
            current_hash,
            self._token_hash(next_refresh_token),
            now,
        )
        if not rotated:
            await self.sessions.revoke_by_session_id(session_id, now)
            raise AuthServiceError(401, "refresh token reuse detected")

        return TokenResponse(
            access_token=next_access_token,
            refresh_token=next_refresh_token,
            expires_in=ACCESS_TOKEN_MINUTES * 60,
            doctor=self._doctor_response(doctor),
        )

    async def authenticate_access_token(self, access_token: str) -> dict:
        payload = self._decode_token(access_token, "access")
        now = _utcnow()
        session = await self.sessions.get_active(payload["sid"], now)
        if not session:
            raise AuthServiceError(401, "session revoked or expired")

        doctor_id = ObjectId(payload["sub"])
        if session.get("doctor_id") != doctor_id:
            raise AuthServiceError(401, "invalid session subject")

        doctor = await self.doctors.get_by_id(doctor_id)
        if not doctor or not doctor.get("is_active", False):
            raise AuthServiceError(401, "inactive account")
        return doctor

    async def logout(self, refresh_token: str, doctor: dict) -> None:
        payload = self._decode_token(refresh_token, "refresh")
        if payload["sub"] != str(doctor["_id"]):
            raise AuthServiceError(401, "invalid refresh subject")

        revoked = await self.sessions.revoke(
            payload["sid"],
            self._token_hash(refresh_token),
            doctor["_id"],
            _utcnow(),
        )
        if not revoked:
            raise AuthServiceError(401, "invalid refresh session")

    def to_doctor_response(self, doctor: dict) -> DoctorResponse:
        return self._doctor_response(doctor)
