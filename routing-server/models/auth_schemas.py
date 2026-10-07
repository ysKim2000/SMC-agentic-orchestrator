import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignupRequest(StrictModel):
    employee_id: str
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    hospital: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    title: str = Field(default="전문의", min_length=1, max_length=100)

    @field_validator("employee_id")
    @classmethod
    def validate_employee_id(cls, value: str) -> str:
        value = value.strip()
        if not EMPLOYEE_ID_PATTERN.fullmatch(value):
            raise ValueError("employee_id must be 3-32 letters, numbers, '.', '_' or '-'")
        return value

    @field_validator("name", "hospital", "department", "title")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class LoginRequest(StrictModel):
    employee_id: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("employee_id")
    @classmethod
    def strip_employee_id(cls, value: str) -> str:
        return value.strip()


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(StrictModel):
    refresh_token: str = Field(min_length=1)


class DoctorResponse(BaseModel):
    employee_id: str
    name: str
    hospital: str
    department: str
    title: str
    initial: str
    roles: list[str]


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    doctor: DoctorResponse
