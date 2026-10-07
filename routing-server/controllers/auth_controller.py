from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from dependencies import get_auth_service, get_current_doctor
from models.auth_schemas import (
    DoctorResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
)
from services.auth_service import AuthService, AuthServiceError

router = APIRouter(tags=["auth"])


def _request_metadata(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    if user_agent:
        user_agent = user_agent[:500]
    return ip, user_agent


def _raise_auth_error(exc: AuthServiceError) -> None:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.detail,
        headers=headers,
    ) from exc


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
):
    try:
        ip, user_agent = _request_metadata(request)
        return await service.signup(body, ip=ip, user_agent=user_agent)
    except AuthServiceError as exc:
        _raise_auth_error(exc)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
):
    try:
        ip, user_agent = _request_metadata(request)
        return await service.login(body, ip=ip, user_agent=user_agent)
    except AuthServiceError as exc:
        _raise_auth_error(exc)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
):
    try:
        return await service.refresh(body.refresh_token)
    except AuthServiceError as exc:
        _raise_auth_error(exc)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: LogoutRequest,
    doctor=Depends(get_current_doctor),
    service: AuthService = Depends(get_auth_service),
):
    try:
        await service.logout(body.refresh_token, doctor)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except AuthServiceError as exc:
        _raise_auth_error(exc)


@router.get("/me", response_model=DoctorResponse)
async def me(
    doctor=Depends(get_current_doctor),
    service: AuthService = Depends(get_auth_service),
):
    return service.to_doctor_response(doctor)
