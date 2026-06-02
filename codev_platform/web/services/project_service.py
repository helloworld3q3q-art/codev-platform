"""Project service —— 项目注册/加载/卸载/查询的业务编排 (plan §三 / §十一 / §十五)。

HTTP routes 不直接访问 repository, 必须经本 service 保持权限与幂等边界。
读写分离: 查重走 read_repo, 落库走 write_repo (plan §十一 ProjectService.register_project 编排)。
错误统一抛 core.errors.PlatformError, 由 app_factory 统一异常处理器转 envelope。
"""
from __future__ import annotations

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.project_id import ProjectIdError, validate
from codev_platform.web.repositories.project_read_repo import ProjectReadRepository
from codev_platform.web.repositories.project_write_repo import ProjectWriteRepository
from codev_platform.web.schemas.projects import ProjectActionResult, ProjectListItem


class ProjectService:
    def __init__(self, read_repo: ProjectReadRepository | None = None,
                 write_repo: ProjectWriteRepository | None = None) -> None:
        self._read = read_repo or ProjectReadRepository()
        self._write = write_repo or ProjectWriteRepository()

    def list_projects(self, *, offset: int, limit: int) -> tuple[list[ProjectListItem], int]:
        """分页列出已登记项目。返回 (页数据, 总数)。"""
        rows = self._read.list_projects()
        total = len(rows)
        page = rows[offset:offset + limit]
        items = [self._to_item(r) for r in page]
        return items, total

    def get_detail(self, code: str) -> ProjectListItem:
        """项目详情。未登记 → PROJECT_UNKNOWN。"""
        code = self._validate_code(code)
        row = self._read.get_project_detail(code)
        if row is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"project not found: {code}")
        return self._to_item(row)

    def register_project(self, *, code: str, name: str,
                         repo_path: str | None, description: str | None) -> ProjectActionResult:
        """注册项目 (幂等: 重复 code 报 INVALID_PARAMS)。

        编排: validate code → 查重 (read_repo) → 落库 (write_repo) → 回执。
        """
        code = self._validate_code(code)
        # 幂等/唯一约束: 已登记 → 报错 (errorCode 细分由路由层填, 主码就近归 INVALID_PARAMS, plan §10.2)。
        if self._read.get_project_detail(code) is not None or self._write.exists(code):
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"project already registered: {code}")
        self._write.register_project(code=code, name=name,
                                     repo_path=repo_path, description=description)
        return ProjectActionResult(code=code, loaded=False, status="ACTIVE")

    def load_project(self, code: str) -> ProjectActionResult:
        """加载项目 (置运行态)。未登记 → PROJECT_UNKNOWN。幂等。"""
        code = self._validate_code(code)
        row = self._read.get_project_detail(code)
        if row is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"project not found: {code}")
        loaded = self._write.set_loaded(code, True)
        return ProjectActionResult(code=code, loaded=loaded, status=row.get("status", "ACTIVE"))

    def unload_project(self, code: str) -> ProjectActionResult:
        """卸载项目 (清运行态)。未登记 → PROJECT_UNKNOWN。幂等。"""
        code = self._validate_code(code)
        row = self._read.get_project_detail(code)
        if row is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"project not found: {code}")
        loaded = self._write.set_loaded(code, False)
        return ProjectActionResult(code=code, loaded=loaded, status=row.get("status", "ACTIVE"))

    def _to_item(self, row: dict) -> ProjectListItem:
        return ProjectListItem(
            code=row["code"], name=row.get("name", ""),
            repoPath=row.get("repoPath"), description=row.get("description"),
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
