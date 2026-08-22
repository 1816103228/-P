"""账号认证：密码散列（pbkdf2，仅用标准库）+ 数据库存储的不透明登录令牌。

- 密码：`hashlib.pbkdf2_hmac("sha256", password, salt, iterations)`，存为
  `pbkdf2$<iterations>$<salt_hex>$<hash_hex>`，每次注册随机盐，可离线验证。
- 令牌：`secrets.token_urlsafe(32)` 随机串，落库 `auth_tokens` 表（可注销、可过期），
  不引入 JWT/额外依赖。有效期由 `config.TOKEN_TTL_DAYS` 控制。
- 调用方：REST 用 `Authorization: Bearer <token>`（get_current_user 依赖）；
  WebSocket 无法带请求头，用查询参数 `?token=`（resolve_token_user）。
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException

import app.db as db
from app import config

_PBKDF2_ITERATIONS = 200_000

_credentials_exc = HTTPException(
    status_code=401,
    detail="登录已失效，请重新登录",
    headers={"WWW-Authenticate": "Bearer"},
)


def hash_password(password: str) -> str:
    """生成 pbkdf2 密码散列：pbkdf2$iterations$salt_hex$hash_hex。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文密码与存储散列是否一致（恒定时间比较）。"""
    try:
        scheme, iterations, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def issue_token(user_id: int) -> str:
    """为用户签发并持久化一个新令牌，返回明文令牌串。"""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=config.TOKEN_TTL_DAYS)).isoformat()
    db.create_auth_token(user_id, token, expires_at)
    return token


def resolve_token_user(token: str | None):
    """按令牌解析用户（供 WebSocket 查询参数认证用）；无效/过期返回 None。"""
    if not token:
        return None
    return db.get_user_by_token(token)


def public_user(user_row) -> dict:
    """把用户行转成可下发给前端的公开信息（不含密码散列）。"""
    return {
        "id": user_row["id"],
        "username": user_row["username"],
        "nickname": user_row["nickname"] or user_row["username"],
        "persona": user_row["persona"] or "",
    }


def get_current_user(authorization: str | None = Header(default=None)):
    """FastAPI 依赖：从 `Authorization: Bearer <token>` 解析当前用户，失败返回 401。"""
    if not authorization:
        raise _credentials_exc
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _credentials_exc
    user = resolve_token_user(token.strip())
    if user is None:
        raise _credentials_exc
    return user


#: 便于测试/复用：等价于 Depends(get_current_user) 的别名
CurrentUser = Depends(get_current_user)
