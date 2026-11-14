"""FastAPI router cung cấp REST API cho dashboard."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import config
from core import users
from core.auth import create_token
from core.engine import BotManager, registry
from core.logger import app_logger, log_handler, log_owner
from core.tunnel import tunnel_manager

router: APIRouter = APIRouter(prefix="/api")


# -----------------------------------------------------------------------------
# Request models
# -----------------------------------------------------------------------------
class LoginBody(BaseModel):
    """Body cho endpoint đăng nhập."""

    username: str
    password: str


class CreateUserBody(BaseModel):
    """Body tạo tài khoản mới (chỉ admin)."""

    username: str
    password: str


class StartBody(BaseModel):
    """Body khởi động bot ở chế độ normal hoặc auto_room."""

    mode: Literal["normal", "auto_room"]
    channel_ids: list[str] = Field(default_factory=list)
    delay: float = 5.0
    leader_count: int = 5


class ToggleBody(BaseModel):
    """Body bật/tắt mic, deaf, video hoặc stream cho một hoặc toàn bộ bot."""

    bot_id: str | None = None
    mute: bool | None = None
    deaf: bool | None = None
    video: bool | None = None
    stream: bool | None = None


class RenameBody(BaseModel):
    """Body đổi nickname cho một hoặc toàn bộ bot."""

    bot_id: str | None = None
    name: str


class MoveBody(BaseModel):
    """Body di chuyển bot tới voice channel khác."""

    target_channel_id: str
    bot_ids: list[str] | None = None


class ReactionBody(BaseModel):
    """Body thả reaction vào một tin nhắn."""

    channel_id: str
    message_id: str
    emoji: str


class JoinBody(BaseModel):
    """Body for making every bot join a server."""

    invite: str
    delay: float = 2.0


class TagBody(BaseModel):
    """Body for equipping / removing the server tag on every bot."""

    guild_id: str = ""
    enable: bool = True
    delay: float = 2.0


class TokensBody(BaseModel):
    """Body cập nhật danh sách token."""

    tokens: list[str]


class ChatSingleBody(BaseModel):
    """Body gửi tin nhắn bằng một bot."""

    bot_id: str
    channel_id: str
    message: str


class ChatAllBody(BaseModel):
    """Body gửi tin nhắn bằng toàn bộ bot ready."""

    channel_id: str
    message: str
    delay: float = 1.0


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _parse_int(value: str, field_name: str) -> int:
    """Đổi chuỗi ID sang int, trả lỗi 400 nếu không hợp lệ."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400, detail=f"{field_name} is invalid: {value}"
        )


def _mask_token(token: str) -> str:
    """Che token để hiển thị an toàn trên dashboard."""
    return f"{token[:6]}..."


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
def _mgr(request: Request) -> BotManager:
    """Bot manager that belongs to the signed-in user (admins: the shared pool).

    Also tags this request's logs with the user's scope so log lines stay private.
    """
    user = request.state.user
    manager = registry.for_user(user)
    log_owner.set(manager.owner)
    return manager


def _require_admin(request: Request) -> dict[str, Any]:
    """Return the current user or raise 403 if they are not an administrator."""
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return user


@router.post("/login", response_model=None)
async def login(body: LoginBody, request: Request) -> dict[str, Any] | JSONResponse:
    """Kiểm tra username/password và trả JWT cho dashboard."""
    ip = request.client.host if request.client else "unknown"
    if users.is_locked_out(ip, body.username):
        app_logger.warning("Đăng nhập bị khoá tạm thời: %s từ %s", body.username, ip)
        return JSONResponse(
            status_code=429,
            content={"error": "Too many attempts. Try again in a few minutes."},
        )

    user = await asyncio.to_thread(users.authenticate, body.username, body.password)
    if user is None:
        users.record_failure(ip, body.username)
        app_logger.warning("Đăng nhập thất bại: %s từ %s", body.username, ip)
        return JSONResponse(status_code=401, content={"error": "Incorrect username or password."})

    users.clear_failures(ip, body.username)
    app_logger.info("Đăng nhập thành công: %s", user["username"])
    return {
        "token": create_token(user),
        "expires_in": config.jwt_expire_hours * 3600,
        "user": {"username": user["username"], "is_admin": user["is_admin"]},
    }




@router.post("/signup", response_model=None)
async def signup(body: CreateUserBody, request: Request) -> dict[str, Any] | JSONResponse:
    """Create a standard member account through the public sign-up form."""
    ip = request.client.host if request.client else "unknown"
    if users.is_locked_out(ip, "signup"):
        return JSONResponse(
            status_code=429,
            content={"error": "Too many sign-up attempts. Try again in a few minutes."},
        )
    try:
        created = await asyncio.to_thread(users.create_user, body.username, body.password)
    except ValueError as exc:
        users.record_failure(ip, "signup")
        raise HTTPException(status_code=400, detail=str(exc))
    users.clear_failures(ip, "signup")
    user = await asyncio.to_thread(users.authenticate, body.username, body.password)
    if user is None:
        raise HTTPException(status_code=500, detail="Account created, but sign-in failed.")
    app_logger.info("New member account registered: %s", created["username"])
    return {
        "token": create_token(user),
        "expires_in": config.jwt_expire_hours * 3600,
        "user": {"username": user["username"], "is_admin": False},
    }


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    """Thông tin người dùng hiện tại."""
    user = request.state.user
    return {"username": user["username"], "is_admin": user["is_admin"]}


@router.get("/users")
async def list_users(request: Request) -> dict[str, Any]:
    """Danh sách tài khoản (admin)."""
    _require_admin(request)
    return {"users": users.list_users()}


@router.post("/users")
async def create_user(body: CreateUserBody, request: Request) -> dict[str, Any]:
    """Tạo tài khoản member (admin)."""
    admin = _require_admin(request)
    try:
        created = await asyncio.to_thread(users.create_user, body.username, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    app_logger.info("%s created account %s", admin["username"], created["username"])
    return {"success": True, "user": created}


@router.post("/users/{user_id}/toggle")
async def toggle_user(user_id: int, request: Request) -> dict[str, Any]:
    """Bật/tắt tài khoản member (admin)."""
    admin = _require_admin(request)
    try:
        updated = users.toggle_user(user_id, admin["id"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated["active"]:
        # A disabled member's bots must stop right away, not keep running.
        member_mgr = registry.existing_member(updated["id"])
        if member_mgr is not None:
            await member_mgr.stop_all()
    app_logger.info("%s changed account status %s", admin["username"], updated["username"])
    return {"success": True, "user": updated}


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Tổng quan trạng thái bot và tunnel."""
    return {**_mgr(request).get_summary(), **tunnel_manager.get_status()}


@router.post("/start")
async def start(body: StartBody, request: Request) -> dict[str, Any]:
    """Khởi động bot ở chế độ normal hoặc auto_room (chạy nền)."""
    manager = _mgr(request)
    channel_ids = [_parse_int(cid, "channel_id") for cid in body.channel_ids]
    if not channel_ids:
        raise HTTPException(status_code=400, detail="At least one channel ID is required.")

    if not manager.tokens:
        raise HTTPException(
            status_code=400, detail="Add at least one token in the Token Manager first"
        )

    if body.mode == "normal":
        asyncio.create_task(manager.start_normal(channel_ids, body.delay))
        message = (
            f"Started normal mode with {len(channel_ids)} channel"
        )
    else:
        lobby_id = channel_ids[0]
        asyncio.create_task(
            manager.start_auto_room(lobby_id, body.leader_count, body.delay)
        )
        message = (
            f"Started auto-room mode at lobby {lobby_id} "
            f"with {body.leader_count} leader"
        )

    app_logger.info(message)
    return {"success": True, "message": message}


@router.post("/stop")
async def stop(request: Request) -> dict[str, Any]:
    """Dừng toàn bộ bot."""
    await _mgr(request).stop_all()
    return {"success": True, "message": "All bots stopped."}


@router.post("/toggle")
async def toggle(body: ToggleBody, request: Request) -> dict[str, Any]:
    """Toggle mic/deaf/video hoặc stream cho một bot hoặc toàn bộ bot."""
    manager = _mgr(request)
    if body.stream is not None:
        if body.bot_id is not None:
            success = await manager.stream_single(body.bot_id, body.stream)
            return {"success": True, "affected": 1 if success else 0}
        affected = await manager.stream_all(body.stream)
        return {"success": True, "affected": affected}

    if body.bot_id is not None:
        success = await manager.toggle_single(
            body.bot_id, mute=body.mute, deaf=body.deaf, video=body.video
        )
        return {"success": True, "affected": 1 if success else 0}

    affected = await manager.toggle_all(
        mute_toggle=bool(body.mute),
        cam_toggle=bool(body.video),
        deaf_toggle=bool(body.deaf),
    )
    return {"success": True, "affected": affected}


@router.post("/rename")
async def rename(body: RenameBody, request: Request) -> dict[str, Any]:
    """Đổi nickname cho một bot hoặc toàn bộ bot."""
    manager = _mgr(request)
    if body.bot_id is not None:
        success = await manager.rename_single(body.bot_id, body.name)
        return {"success": True, "affected": 1 if success else 0}

    affected = await manager.rename_all(body.name)
    return {"success": True, "affected": affected}


@router.post("/move")
async def move(body: MoveBody, request: Request) -> dict[str, Any]:
    """Di chuyển bot tới voice channel chỉ định (join ngay và cập nhật state)."""
    manager = _mgr(request)
    target_channel_id = _parse_int(body.target_channel_id, "target_channel_id")
    moved = await manager.move_bots(target_channel_id, body.bot_ids)
    return {"success": True, "moved": moved}


@router.get("/bots/{bot_id}/voice-channels")
async def get_bot_voice_channels(bot_id: str, request: Request) -> dict[str, Any]:
    """Liệt kê voice/stage channel khả dụng cho một bot."""
    manager = _mgr(request)
    channels = manager.get_available_voice_channels(bot_id)
    return {"channels": channels}


@router.post("/chat/single")
async def chat_single(body: ChatSingleBody, request: Request) -> dict[str, Any]:
    """Gửi tin nhắn bằng một bot tới text channel chỉ định."""
    manager = _mgr(request)
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    channel_id = _parse_int(body.channel_id, "channel_id")
    sent = await manager.chat_single(body.bot_id, channel_id, message)
    return {"success": True, "sent": 1 if sent else 0}


@router.post("/chat/all")
async def chat_all(body: ChatAllBody, request: Request) -> dict[str, Any]:
    """Gửi tin nhắn bằng toàn bộ bot ready with delay giữa mỗi bot."""
    manager = _mgr(request)
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    channel_id = _parse_int(body.channel_id, "channel_id")
    delay = max(0.0, body.delay)
    sent = await manager.chat_all(channel_id, message, delay)
    return {"success": True, "sent": sent}


@router.post("/reaction")
async def reaction(body: ReactionBody, request: Request) -> dict[str, Any]:
    """Cho toàn bộ bot thả reaction vào tin nhắn chỉ định."""
    manager = _mgr(request)
    channel_id = _parse_int(body.channel_id, "channel_id")
    message_id = _parse_int(body.message_id, "message_id")
    reacted = await manager.spam_reaction(channel_id, message_id, body.emoji)
    return {"success": True, "reacted": reacted}


def _spawn(manager: BotManager, coro: Any) -> None:
    """Run a long bulk action in the background; one at a time per manager."""
    task = manager.busy_task
    if task is not None and not task.done():
        coro.close()
        raise HTTPException(status_code=409, detail="Another bulk action is still running.")
    manager.busy_task = asyncio.create_task(coro)


@router.post("/join")
async def join_server(body: JoinBody, request: Request) -> dict[str, Any]:
    """Every ready bot joins the server behind the given invite link."""
    manager = _mgr(request)
    code = manager.parse_invite_code(body.invite)
    if not code or len(code) > 64:
        raise HTTPException(status_code=400, detail="Invalid invite link.")
    ready = sum(1 for bot in manager.bots if bot.is_ready())
    _spawn(manager, manager.join_all(code, body.delay))
    return {"success": True, "queued": ready}


@router.post("/tag")
async def equip_tag(body: TagBody, request: Request) -> dict[str, Any]:
    """Equip (or remove) the server tag on every ready bot."""
    manager = _mgr(request)
    gid = body.guild_id.strip() or manager.last_guild_id or ""
    if body.enable:
        _parse_int(gid, "guild_id")
    ready = sum(1 for bot in manager.bots if bot.is_ready())
    _spawn(manager, manager.equip_tags(gid, body.enable, body.delay))
    return {"success": True, "queued": ready, "guild_id": gid}


@router.get("/tokens")
async def get_tokens(request: Request) -> dict[str, Any]:
    """Trả danh sách token của CHÍNH người dùng này (mỗi người một danh sách riêng)."""
    manager = _mgr(request)
    tokens = list(manager.tokens)
    return {
        "count": len(tokens),
        "max": manager.max_tokens,
        "tokens": [
            {"index": index, "masked": _mask_token(token), "full": token}
            for index, token in enumerate(tokens)
        ],
    }


@router.put("/tokens")
async def update_tokens(body: TokensBody, request: Request) -> dict[str, Any]:
    """Lưu danh sách token của người dùng hiện tại."""
    manager = _mgr(request)
    try:
        await asyncio.to_thread(manager.save_tokens, body.tokens)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"success": True, "count": len(manager.tokens)}


@router.get("/logs")
async def get_logs(request: Request) -> dict[str, Any]:
    """Trả các log gần đây mà người dùng này được phép xem."""
    user = request.state.user
    scope = registry.scope_of(user)
    return {"logs": log_handler.get_recent_logs(scope, bool(user["is_admin"]))}


@router.delete("/logs")
async def delete_logs(request: Request) -> dict[str, Any]:
    """Xoá các log của người dùng này (admin xoá toàn bộ)."""
    user = request.state.user
    log_handler.clear_logs(registry.scope_of(user), bool(user["is_admin"]))
    return {"success": True}
