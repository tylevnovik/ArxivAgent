"""
本地 API HMAC 身份验证模块。

设计思路：
- 后端每次启动生成一对随机密钥（secret + token）。
- secret 用于 HMAC 签名，token 作为公开标识。
- 前端用 HMAC-SHA256 签名每个请求的时间戳 + 方法 + 路径。
- 后端验证签名，拒绝过期（±60s）或伪造的请求。

安全性：即使 token 泄露，没有 secret 也无法伪造有效签名。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

# ===================== 密钥生成 =====================

# 每个 token/secret 32 字节（256 bit），hex 编码后 64 字符。
_KEY_LENGTH = 32


def generate_keypair() -> tuple[bytes, bytes]:
    """生成 (secret, token) 随机密钥对，各 32 字节。"""
    secret = os.urandom(_KEY_LENGTH)
    token = os.urandom(_KEY_LENGTH)
    return secret, token


# ===================== 签名计算 =====================

TIMESTAMP_TOLERANCE_SECONDS = 60


def compute_hmac(secret: bytes, timestamp: int, method: str, path: str) -> str:
    """
    计算 HMAC-SHA256 签名，返回 hex 字符串。

    消息格式："{timestamp}\n{METHOD}\n{path}"
    """
    message = f"{timestamp}\n{method}\n{path}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def build_auth_header(secret: bytes, token: bytes, method: str, path: str) -> str:
    """
    构造 Authorization 头值。

    格式：Hmac {token_hex}:{timestamp}:{signature_hex}
    """
    ts = int(time.time())
    token_hex = token.hex()
    sig = compute_hmac(secret, ts, method, path)
    return f"Hmac {token_hex}:{ts}:{sig}"


# ===================== 签名验证 =====================

class AuthError(Exception):
    """认证失败时抛出的异常。"""


def verify_request(
    secret: bytes,
    token: bytes,
    authorization: Optional[str],
    method: str,
    path: str,
) -> None:
    """
    验证请求的 Authorization 头。验证通过则静默返回，失败抛出 AuthError。

    Raises:
        AuthError: token 缺失/格式错误/签名不匹配/时间戳过期。
    """
    if not authorization:
        raise AuthError("缺少 Authorization 头")

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0] != "Hmac":
        raise AuthError("Authorization 格式无效，预期 'Hmac <token>:<ts>:<sig>'")

    cred = parts[1]
    cred_parts = cred.split(":")
    if len(cred_parts) != 3:
        raise AuthError("Hmac 凭证格式无效，预期 '<token>:<timestamp>:<signature>'")

    req_token_hex, ts_str, req_sig = cred_parts

    # 验证 token
    if not hmac.compare_digest(req_token_hex, token.hex()):
        raise AuthError("token 不匹配")

    # 验证时间戳（防重放）
    try:
        req_ts = int(ts_str)
    except ValueError:
        raise AuthError("时间戳格式无效")

    now = int(time.time())
    if abs(now - req_ts) > TIMESTAMP_TOLERANCE_SECONDS:
        raise AuthError(f"请求已过期（时间戳偏差 {abs(now - req_ts)}s，容许 {TIMESTAMP_TOLERANCE_SECONDS}s）")

    # 验证 HMAC 签名
    expected_sig = compute_hmac(secret, req_ts, method, path)
    if not hmac.compare_digest(req_sig, expected_sig):
        raise AuthError("HMAC 签名不匹配")


# ===================== 中间件集成 =====================

def unauthorized_response(detail: str = "未授权") -> JSONResponse:
    """返回 401 JSON 响应。"""
    return JSONResponse(
        status_code=401,
        content={
            "ok": False,
            "error": {
                "code": "unauthorized",
                "message": detail,
                "recoverable": True,
            },
        },
    )


# 不需要认证的路径白名单。
PUBLIC_PATHS = {"/api/health", "/api/system/deps", "/api/auth/status"}


def is_public_path(path: str) -> bool:
    """判断路径是否在认证白名单中。"""
    return path in PUBLIC_PATHS
