"""Project service —— 项目注册/加载/卸载/查询的业务编排 (plan §三 / §十一 / §十五)。

HTTP routes 不直接访问 repository, 必须经本 service 保持权限与幂等边界。
读写分离: 查重走 read_repo, 落库走 write_repo (plan §十一 ProjectService.register_project 编排)。
错误统一抛 core.errors.PlatformError, 由 app_factory 统一异常处理器转 envelope。
"""
from __future__ import annotations

from codev_platform.core.config import get as _cfg_get
from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.platform_admin import is_platform_admin
from codev_platform.core.project_id import ProjectIdError, validate
from codev_platform.web.repositories.project_read_repo import ProjectReadRepository
from codev_platform.web.repositories.project_write_repo import ProjectWriteRepository
from codev_platform.web.schemas.projects import ProjectActionResult, ProjectListItem
from codev_platform.web.security.membership import can_access_project, is_org_admin
from codev_platform.web.security.sessions import Session


def _project_in_org(cfg: dict, row: dict, org_id: str) -> bool:
    """项目是否属于该 org —— 与 core.acl 同口径: 项目 org_id 缺省=公开(全 org 可见), 否则须 == org_id。

    org_id 真值源优先 meta.json (register 落库于此), 回退 config projects.<code>.org_id (兼容历史登记)。
    """
    proj_org = row.get("orgId")
    if proj_org is None:
        proj = (_cfg_get(cfg, "projects", {}) or {}).get(row.get("code")) or {}
        proj_org = proj.get("org_id")
    return proj_org is None or proj_org == org_id


def session_project_decision(identity, project_id, action: str = "read"):
    """require_project_access 的 via=session 授权(core 注入点; 见 permissions._session_checker)。

    复刻 _authorize_project 三步(单一真值源, 不另造): platform_admin bypass → org 隔离
    (_project_in_org, 防 org admin 跨 org) → 逐项目 role(can_access_project)。core/httpkit 不 import
    web, 故由 web 启动把本函数注入 permissions; MCP/agent 进程不注入 → session 身份恒 deny。
    无显式 project_id / 项目未登记 / 跨 org / 无 role → deny(对齐 token 模式防越权)。
    """
    from codev_platform.core.acl import AccessDecision
    if not project_id:
        return AccessDecision(False, "session: no explicit project_id")
    username = getattr(identity, "user_id", None)
    org_id = getattr(identity, "org_id", None)
    cfg = load_config()
    if is_platform_admin(cfg, username):
        return AccessDecision(True, "session: platform admin (cross-org)", advisory=False)
    row = ProjectReadRepository().get_project_detail(project_id)
    if row is None:
        return AccessDecision(False, f"session: project not registered: {project_id}")
    if not _project_in_org(cfg, row, org_id):
        return AccessDecision(False, "session: project not in user's org")
    # Session 只需 username/org_id 参与授权(时间字段不参与, 填 0)。
    sess = Session(session_id="", username=username or "", org_id=org_id or "",
                   access_expires_at=0.0, refresh_expires_at=0.0)
    if not can_access_project(sess, project_id, action):
        return AccessDecision(False, f"session: no project {action} access")
    return AccessDecision(True, "session: web RBAC membership")


class ProjectService:
    def __init__(self, read_repo: ProjectReadRepository | None = None,
                 write_repo: ProjectWriteRepository | None = None) -> None:
        self._read = read_repo or ProjectReadRepository()
        self._write = write_repo or ProjectWriteRepository()

    def list_projects(
        self, *, sess: Session, filter_org_id: str | None = None,
        keyword: str | None = None, offset: int, limit: int
    ) -> tuple[list[ProjectListItem], int]:
        """分页列出**当前会话可见**的项目 (org 隔离 + 逐项目可见性)。

        - platform_admin: 看全部 (跨 org)。
        - org admin: 看本 org 全部项目。
        - 普通成员: 仅本 org 内、且自己有 project_role 的项目。
        filter_org_id: 在可见集内进一步收窄到该组织 (项目无 org_id = 公开, 全 org 可见)。
        keyword: 模糊匹配 code / name (大小写不敏感)。
        """
        rows = self._read.list_projects()
        cfg = load_config()
        if not is_platform_admin(cfg, sess.username):
            # org 隔离: 只留本 org (含公开) 项目
            rows = [r for r in rows if _project_in_org(cfg, r, sess.org_id)]
            # 逐项目可见性: 非 org admin 只看自己有 project_role 的项目
            if not is_org_admin(sess):
                rows = [r for r in rows if can_access_project(sess, r["code"], "read")]
        if filter_org_id is not None:
            rows = [r for r in rows if _project_in_org(cfg, r, filter_org_id)]
        if keyword:
            kw = keyword.strip().lower()
            if kw:
                rows = [r for r in rows
                        if kw in (r.get("code") or "").lower()
                        or kw in (r.get("name") or "").lower()]
        total = len(rows)
        page = rows[offset:offset + limit]
        items = [self._to_item(r) for r in page]
        return items, total

    def get_detail(self, code: str, sess: Session) -> ProjectListItem:
        """项目详情。未登记 → PROJECT_UNKNOWN; 无 read 权限 → ACCESS_DENIED。"""
        code = self._validate_code(code)
        row = self._read.get_project_detail(code)
        if row is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"project not found: {code}")
        self._authorize_project(sess, row, "read")
        return self._to_item(row)

    def _authorize_project(self, sess: Session, row: dict, action: str) -> None:
        """项目级授权闸: org 隔离(项目 org != 会话 org 则拒) + 逐项目 role_allows(action)。

        platform_admin 跨 org 放行; org admin / 有授予 project_role 的成员按 rank 判 action。
        """
        cfg = load_config()
        if is_platform_admin(cfg, sess.username):
            return
        if not _project_in_org(cfg, row, sess.org_id):
            raise PlatformError(ErrorCode.ACCESS_DENIED, "项目不属于当前组织")
        if not can_access_project(sess, row["code"], action):
            raise PlatformError(ErrorCode.ACCESS_DENIED, f"需要项目 {action} 权限")

    def register_project(self, *, code: str, name: str,
                         repo_path: str | None, description: str | None,
                         org_id: str | None = None) -> ProjectActionResult:
        """注册项目 (幂等: 重复 code 报 INVALID_PARAMS)。

        编排: validate code → 查重 (read_repo) → 落库 (write_repo) → 回执。
        org_id 为项目归属组织 (None = 公开; 由路由层用当前请求 org 兜底)。
        """
        code = self._validate_code(code)
        # 幂等/唯一约束: 已登记 → 报错 (errorCode 细分由路由层填, 主码就近归 INVALID_PARAMS, plan §10.2)。
        if self._read.get_project_detail(code) is not None or self._write.exists(code):
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"project already registered: {code}")
        org_id = org_id.strip() if org_id else None
        # L2(审计根治): token(多租户)模式下 org_id 必填 —— org-less=公开(任意 org admin 可对它
        # write: reindex/跑 agent/load)是单机便利, 多租户下是越权根源(meta.json 从不写 orgId 是病根)。
        # 强制项目归属, 让所有项目走标准 org 隔离 + 白名单; dev passthrough 单机仍允许 org-less。
        if org_id is None and _cfg_get(load_config(), "gateway.auth_mode", "passthrough") == "token":
            raise PlatformError(
                ErrorCode.INVALID_PARAMS,
                "token(多租户)模式下注册项目必须指定 org_id(防 org-less 公开项目被任意 org admin 越权 write)",
            )
        self._write.register_project(code=code, name=name,
                                     repo_path=repo_path, description=description, org_id=org_id)
        return ProjectActionResult(code=code, loaded=False, status="ACTIVE")

    def load_project(self, code: str, sess: Session) -> ProjectActionResult:
        """加载项目 (置运行态)。未登记 → PROJECT_UNKNOWN; 无 write 权限 → ACCESS_DENIED。幂等。"""
        code = self._validate_code(code)
        row = self._read.get_project_detail(code)
        if row is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"project not found: {code}")
        self._authorize_project(sess, row, "write")
        loaded = self._write.set_loaded(code, True)
        return ProjectActionResult(code=code, loaded=loaded, status=row.get("status", "ACTIVE"))

    def unload_project(self, code: str, sess: Session) -> ProjectActionResult:
        """卸载项目 (清运行态)。未登记 → PROJECT_UNKNOWN; 无 write 权限 → ACCESS_DENIED。幂等。"""
        code = self._validate_code(code)
        row = self._read.get_project_detail(code)
        if row is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"project not found: {code}")
        self._authorize_project(sess, row, "write")
        loaded = self._write.set_loaded(code, False)
        return ProjectActionResult(code=code, loaded=loaded, status=row.get("status", "ACTIVE"))

    def _to_item(self, row: dict) -> ProjectListItem:
        # orgId 真值源优先 meta.json (row), 回退 config projects.<code>.org_id (兼容历史登记, 与 _project_in_org 同口径)。
        org_id = row.get("orgId")
        if org_id is None:
            cfg = load_config()
            proj = (_cfg_get(cfg, "projects", {}) or {}).get(row.get("code")) or {}
            org_id = proj.get("org_id")
        return ProjectListItem(
            code=row["code"], name=row.get("name", ""),
            repoPath=row.get("repoPath"), description=row.get("description"),
            orgId=org_id,
            status=row.get("status", "ACTIVE"),
            loaded=self._write.is_loaded(row["code"]),
        )

    @staticmethod
    def _validate_code(code: str) -> str:
        """复用 core.project_id slug 约束; 非法 → INVALID_PARAMS (服务端再次校验, plan §八)。"""
        try:
            return validate(code)
        except ProjectIdError as exc:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"invalid project code: {code}",
                                detail=str(exc)) from exc
