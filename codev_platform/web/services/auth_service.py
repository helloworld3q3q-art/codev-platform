"""Auth service —— 登录 / 登出 / 刷新的业务编排 (plan §三 / §十五 Auth)。

HTTP routes 不直接访问 store, 必须经本 service 保持校验与签发边界。
登录校验: 用户存在 + 状态 ACTIVE + 密码匹配, 任一不满足统一返回 ACCESS_DENIED
(不区分"用户不存在"/"密码错"对外文案, 防用户名枚举; detail 进日志区分)。
密码只 verify, 明文绝不落库 / 不日志 (security.md)。
错误统一抛 core.errors.PlatformError, 由 app_factory 异常处理器转 envelope。
"""
from __future__ import annotations

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.web.domain.accounts import STATUS_ACTIVE
from codev_platform.web.repositories.account_store import get_member_store, get_user_store
from codev_platform.web.schemas.auth import TokenPair
from codev_platform.web.security.passwords import verify_password
from codev_platform.web.security.rsa_keys import get_keypair
from codev_platform.web.security.sessions import IssuedTokens, get_session_store


class AuthService:
    @staticmethod
    def _maybe_decrypt(password: str) -> str:
        """口令可能是前端 RSA 加密 (方案 B) → 解密; 非密文 (明文/测试) 原样返回。"""
        kp = get_keypair()
        if kp is not None:
            dec = kp.decrypt_b64(password)
            if dec is not None:
                return dec
        return password

    def login(self, *, username: str, password: str) -> TokenPair:
        """校验凭据 → 签发会话。失败统一 ACCESS_DENIED (detail 区分原因, 仅进日志)。"""
        password = self._maybe_decrypt(password)
        user = get_user_store().get(username)
        if user is None:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "AUTH_LOGIN_FAILED", detail="user not found")
        if user.status != STATUS_ACTIVE:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "AUTH_LOGIN_FAILED", detail="user disabled")
        if not verify_password(password, user.password_hash):
            raise PlatformError(ErrorCode.ACCESS_DENIED, "AUTH_LOGIN_FAILED", detail="bad password")
        issued = get_session_store().create(user.username, user.org_id)
        return self._to_pair(issued)

    def refresh(self, *, refresh_token: str) -> TokenPair:
        """refresh token 换新 token 对。失效 / 过期 → ACCESS_DENIED。"""
        issued = get_session_store().refresh(refresh_token)
        if issued is None:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "AUTH_REFRESH_FAILED", detail="invalid refresh token")
        return self._to_pair(issued)

    def switch_org(self, *, username: str, target_org: str,
                   caller_is_platform_admin: bool) -> TokenPair:
        """切换活动组织 → 重签 session(新 org_id), RBAC 随后按新 org 判。

        授权(跨 org 隔离红线): **非 platform_admin 必须是目标 org 成员**才能切 —— 否则任意登录用户
        改个 orgId 就能拿到别 org 的会话身份 = 越权。platform_admin 可切任意 org(运维特权)。
        旧 session 不主动撤(短 TTL 自然过期); 前端换用新 token。
        """
        target_org = (target_org or "").strip()
        if not caller_is_platform_admin and get_member_store().get(target_org, username) is None:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "不是该组织成员, 不能切换到此组织")
        issued = get_session_store().create(username, target_org)
        return self._to_pair(issued)

    def logout(self, *, refresh_token: str) -> None:
        """登出: 撤销 refresh token 对应会话 (幂等; 未知 token 静默放行)。"""
        get_session_store().revoke(refresh_token)

    @staticmethod
    def _to_pair(issued: IssuedTokens) -> TokenPair:
        return TokenPair(accessToken=issued.access_token, refreshToken=issued.refresh_token)
