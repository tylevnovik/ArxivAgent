"""
HMAC 认证模块测试。

验证：
- 密钥对生成
- 签名计算与验证
- 各种认证失败场景
- FastAPI 中间件对受保护/公开路径的处理
"""

import time

import pytest

from core.auth import (
    AuthError,
    compute_hmac,
    generate_keypair,
    is_public_path,
    build_auth_header,
    verify_request,
)


# ===================== 单元测试 =====================


class TestGenerateKeypair:
    def test_returns_32_bytes_each(self):
        secret, token = generate_keypair()
        assert len(secret) == 32
        assert len(token) == 32

    def test_keys_are_random(self):
        s1, t1 = generate_keypair()
        s2, t2 = generate_keypair()
        # 极小概率碰撞，但 32 字节随机数碰上基本不可能
        assert s1 != s2 or t1 != t2

    def test_keys_are_bytes(self):
        secret, token = generate_keypair()
        assert isinstance(secret, bytes)
        assert isinstance(token, bytes)


class TestComputeHmac:
    def test_deterministic(self):
        secret = b"test-secret-key-1234567890123456"
        ts = 1700000000
        sig1 = compute_hmac(secret, ts, "GET", "/api/threads")
        sig2 = compute_hmac(secret, ts, "GET", "/api/threads")
        assert sig1 == sig2

    def test_different_input_different_output(self):
        secret = b"test-secret-key-1234567890123456"
        sig1 = compute_hmac(secret, 1700000000, "GET", "/api/threads")
        sig2 = compute_hmac(secret, 1700000001, "GET", "/api/threads")
        sig3 = compute_hmac(secret, 1700000000, "POST", "/api/threads")
        sig4 = compute_hmac(secret, 1700000000, "GET", "/api/other")
        assert sig1 != sig2
        assert sig1 != sig3
        assert sig1 != sig4

    def test_hex_output(self):
        secret = b"test-secret-key-1234567890123456"
        sig = compute_hmac(secret, 1700000000, "GET", "/api/test")
        # hex 字符串：64 字符（32 字节 * 2）
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)


class TestBuildAuthHeader:
    def test_format(self):
        secret = b"secret-12345678901234567890123456"
        token = b"token-12345678901234567890123456"
        header = build_auth_header(secret, token, "GET", "/api/threads")
        assert header.startswith("Hmac ")
        parts = header[5:].split(":")
        assert len(parts) == 3  # token:timestamp:signature
        assert parts[0] == token.hex()
        assert len(parts[1]) > 0  # timestamp
        assert len(parts[2]) == 64  # signature hex


class TestVerifyRequest:
    def _make_valid_header(self, secret, token, method, path):
        ts = int(time.time())
        sig = compute_hmac(secret, ts, method, path)
        return f"Hmac {token.hex()}:{ts}:{sig}"

    def test_valid_request_passes(self):
        secret, token = generate_keypair()
        header = self._make_valid_header(secret, token, "GET", "/api/threads")
        verify_request(secret, token, header, "GET", "/api/threads")  # 不抛异常

    def test_missing_authorization(self):
        secret, token = generate_keypair()
        with pytest.raises(AuthError, match="缺少"):
            verify_request(secret, token, None, "GET", "/api/threads")

    def test_wrong_scheme(self):
        secret, token = generate_keypair()
        with pytest.raises(AuthError, match="格式无效"):
            verify_request(secret, token, "Bearer something", "GET", "/api/threads")

    def test_wrong_token(self):
        secret, token = generate_keypair()
        _, other_token = generate_keypair()
        # 用另一个 token 签名，但传原来的 token hex
        wrong_header = f"Hmac {other_token.hex()}:{int(time.time())}:fakesig"
        with pytest.raises(AuthError, match="token 不匹配"):
            verify_request(secret, token, wrong_header, "GET", "/api/threads")

    def test_wrong_signature(self):
        secret, token = generate_keypair()
        header = f"Hmac {token.hex()}:{int(time.time())}:a" + "0" * 63
        with pytest.raises(AuthError, match="签名不匹配"):
            verify_request(secret, token, header, "GET", "/api/threads")

    def test_expired_timestamp(self):
        secret, token = generate_keypair()
        # 2 分钟前的时间戳
        old_ts = int(time.time()) - 120
        sig = compute_hmac(secret, old_ts, "GET", "/api/threads")
        header = f"Hmac {token.hex()}:{old_ts}:{sig}"
        with pytest.raises(AuthError, match="过期"):
            verify_request(secret, token, header, "GET", "/api/threads")

    def test_future_timestamp(self):
        secret, token = generate_keypair()
        # 2 分钟后的时间戳
        future_ts = int(time.time()) + 120
        sig = compute_hmac(secret, future_ts, "GET", "/api/threads")
        header = f"Hmac {token.hex()}:{future_ts}:{sig}"
        with pytest.raises(AuthError, match="过期"):
            verify_request(secret, token, header, "GET", "/api/threads")

    def test_wrong_method(self):
        secret, token = generate_keypair()
        # 用 GET 签名但验证 POST
        header = self._make_valid_header(secret, token, "GET", "/api/threads")
        with pytest.raises(AuthError, match="签名不匹配"):
            verify_request(secret, token, header, "POST", "/api/threads")


class TestPublicPaths:
    def test_health_is_public(self):
        assert is_public_path("/api/health") is True

    def test_system_deps_is_public(self):
        assert is_public_path("/api/system/deps") is True

    def test_config_health_is_public(self):
        assert is_public_path("/api/config/health") is True

    def test_threads_is_protected(self):
        assert is_public_path("/api/threads") is False

    def test_thread_message_is_protected(self):
        assert is_public_path("/api/threads/abc123/messages") is False

    def test_auth_status_is_public(self):
        assert is_public_path("/api/auth/status") is True


# ===================== 集成测试（FastAPI TestClient） =====================


class TestAuthMiddleware:
    """验证认证中间件在 FastAPI 中的行为。注意：需要 AUTH_ENABLED=true。"""

    @pytest.fixture
    def auth_client(isolated_data_dir):
        """启用认证的 TestClient。"""
        import importlib
        import config as cfg
        import app as appmod

        # 确保 AUTH_ENABLED 开启
        cfg.AUTH_ENABLED = True

        # reload app 模块
        importlib.reload(appmod)

        from fastapi.testclient import TestClient
        # TestClient 的 with 块会触发 startup 事件，密钥在其中生成
        with TestClient(appmod.app) as c:
            # 读取 startup 生成的密钥对
            secret = appmod._auth_secret
            token = appmod._auth_token
            yield c, secret, token

    def _make_header(self, secret, token, method, path):
        return build_auth_header(secret, token, method, path)

    def test_public_endpoints_no_auth_needed(self, auth_client):
        c, _, _ = auth_client
        r = c.get("/api/health")
        assert r.status_code == 200
        r = c.get("/api/system/deps")
        assert r.status_code == 200

    def test_protected_endpoint_rejects_without_auth(self, auth_client):
        c, _, _ = auth_client
        r = c.get("/api/threads")
        assert r.status_code == 401
        data = r.json()
        assert data["error"]["code"] == "unauthorized"

    def test_protected_endpoint_accepts_valid_auth(self, auth_client):
        c, secret, token = auth_client
        headers = {"Authorization": self._make_header(
            secret, token, "GET", "/api/threads",
        )}
        r = c.get("/api/threads", headers=headers)
        assert r.status_code == 200

    def test_protected_endpoint_rejects_wrong_auth(self, auth_client):
        c, _, token = auth_client
        # 用一个错误的签名
        headers = {"Authorization": f"Hmac {token.hex()}:{int(time.time())}:fakesignature"}
        r = c.get("/api/threads", headers=headers)
        assert r.status_code == 401

    def test_auth_status_endpoint(self, auth_client):
        c, _, _ = auth_client
        r = c.get("/api/auth/status")
        assert r.status_code == 200
        data = r.json()
        assert data["auth_enabled"] is True
        assert data["auth_scheme"] == "HMAC-SHA256"
