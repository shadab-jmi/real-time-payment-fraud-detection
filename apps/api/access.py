import hashlib
import os
import secrets
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from packages.contracts.stream import query
from packages.db.connect import engine

router = APIRouter()
basic = HTTPBasic(auto_error=False)
attempts = {}
lock = threading.Lock()
dummy = PasswordHasher().hash(secrets.token_urlsafe(24))


class Login(BaseModel):
    username: str = Field(min_length=1,max_length=80)
    password: str = Field(min_length=1,max_length=512)


def origin(request):
    port = os.getenv("WEB_PORT","3000")
    public = os.getenv("PUBLIC_ORIGIN")
    allowed = {public} if public else {f"http://localhost:{port}", f"http://127.0.0.1:{port}"}
    if request.headers.get("origin") not in allowed:
        raise HTTPException(403,"Invalid request origin")


def client_ip(request):
    # In production Uvicorn resolves X-Forwarded-For only for the Caddy peer.
    if os.getenv("PUBLIC_ORIGIN"):
        return request.client.host
    return request.headers.get("x-real-ip",request.client.host)


def secure_cookie(request):
    return bool(os.getenv("PUBLIC_ORIGIN")) or request.url.scheme == "https"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def authenticate(request, username, password):
    now = time.monotonic()
    keys = [("ip",client_ip(request)),("user",username)]
    with lock:
        for key in list(attempts):
            while attempts[key] and attempts[key][0]<now-60:
                attempts[key].popleft()
            if not attempts[key]:
                del attempts[key]
        if any(len(attempts.get(k,())) >= 12 for k in keys):
            raise HTTPException(429,"Too many attempts. Try again in one minute.")
        for key in keys:
            attempts.setdefault(key,deque()).append(now)
    with engine.connect() as c:
        user=query(c,"SELECT id,username,role,password_hash FROM users WHERE username=:u AND NOT disabled",u=username).mappings().one_or_none()
    try:
        PasswordHasher().verify(user["password_hash"] if user else dummy,password)
    except VerificationError:
        raise HTTPException(401,"Invalid username or password")
    if not user:
        raise HTTPException(401,"Invalid username or password")
    return {k:user[k] for k in ("id","username","role")}


@router.post("/v1/session")
def login(data: Login,request: Request,response: Response):
    origin(request)
    u=authenticate(request,data.username,data.password)
    token=secrets.token_urlsafe(32)
    with engine.begin() as c:
        query(c,"INSERT INTO sessions VALUES(:h,:u,now()+interval '8 hours')",h=digest(token),u=u["id"])
    response.set_cookie("session",token,httponly=True,samesite="strict",max_age=28800,secure=secure_cookie(request))
    return {**u,"csrf":digest("csrf/"+token)}


def session_user(request: Request):
    token=request.cookies.get("session","")
    if not token:
        return None
    with engine.connect() as c:
        u=query(c,"""SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE token_hash=:h AND expires_at>now() AND NOT disabled""",h=digest(token)).mappings().one_or_none()
    return {**u,"csrf":digest("csrf/"+token)} if u else None


def user(request: Request):
    u = session_user(request)
    if not u:
        raise HTTPException(401,"Sign in to continue")
    return u


def detail_reader(request: Request, credentials: HTTPBasicCredentials|None=Depends(basic)):
    if credentials:
        return authenticate(request,credentials.username,credentials.password)
    return user(request)


def mutation(request: Request,u=Depends(user)):
    origin(request)
    if not secrets.compare_digest(request.headers.get("x-csrf-token","").encode(),u["csrf"].encode()):
        raise HTTPException(403,"Invalid CSRF token")
    return u


def analyst(u=Depends(mutation)):
    if u["role"]!="analyst":
        raise HTTPException(403,"Analyst access required")
    return u


@router.get("/v1/me")
def me(u=Depends(user)):
    return u


@router.delete("/v1/session")
def logout(request: Request,response: Response,u=Depends(mutation)):
    with engine.begin() as c:
        query(c,"DELETE FROM sessions WHERE token_hash=:h",h=digest(request.cookies["session"]))
    response.delete_cookie("session", secure=secure_cookie(request), httponly=True, samesite="strict")
    return {"signed_out":True}
