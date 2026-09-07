"""`codev-platform onboard` 编排命令测试 —— 一条命令完成登记(--no-index 不触索引)。

验证编排器把 config / project.json / meta.json 三处登记一次写齐, 软步(RBAC / codegraph)
不可用时优雅跳过且不阻断(rc=0)。复用既有能力, 不重造的接缝在此钉死。
"""
from __future__ import annotations

import argparse
import json

from codev_platform.ops.onboard import cmd_onboard
from codev_platform.reindex.queue import JobMeta
from codev_platform.index_kind_contract import REQUIRED_INDEX_KINDS


def _args(code, repo, **kw):
    base = dict(code=code, repo=str(repo), org="default", owner="root", name=None,
                mcp_source="platform", no_index=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_onboard_no_index_writes_all_registration(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    meta_root = tmp_path / "platform_meta" / "projects"
    cfg_store: dict = {"projects": {}}
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg_store)
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)  # cfg_store 原地改即可
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", meta_root)

    rc = cmd_onboard(_args("myproj", repo))
    assert rc == 0

    # 1. config projects.<code>
    assert cfg_store["projects"]["myproj"]["repo_path"] == str(repo.resolve())
    assert cfg_store["projects"]["myproj"]["org_id"] == "default"
    # 2. <repo>/.claude/project.json
    pj = json.loads((repo / ".claude" / "project.json").read_text(encoding="utf-8"))
    assert pj["project_id"] == "myproj" and pj["display_name"] == "myproj"
    # 3. meta.json
    meta = json.loads((meta_root / "myproj" / "meta.json").read_text(encoding="utf-8"))
    assert meta["project_id"] == "myproj"
    assert meta["repo_path"] == str(repo.resolve())


def test_onboard_generates_codegraph_config_and_gitignore(tmp_path, monkeypatch):
    """codegraph config.json(带排除)+ gitignore db 规则在仓里生成(可提交, 随 git 走)。"""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", tmp_path / "meta")

    rc = cmd_onboard(_args("p", repo))
    assert rc == 0
    # .codegraph/config.json 生成 + 含关键排除(min.js / vendored / public 噪声)
    cg = json.loads((repo / ".codegraph" / "config.json").read_text(encoding="utf-8"))
    assert "**/*.min.js" in cg["exclude"] and "**/public/**" in cg["exclude"]
    assert "**/*.java" in cg["include"]
    # .gitignore 追加 db 忽略(保留原有 node_modules)
    gi = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gi and ".codegraph/codegraph.db" in gi


def test_onboard_preserves_existing_codegraph_config(tmp_path, monkeypatch):
    """已有 .codegraph/config.json 不覆盖(保用户自定义)。"""
    repo = tmp_path / "r"
    (repo / ".codegraph").mkdir(parents=True)
    (repo / ".codegraph" / "config.json").write_text('{"custom": true}', encoding="utf-8")
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", tmp_path / "meta")
    rc = cmd_onboard(_args("p", repo))
    assert rc == 0
    assert json.loads((repo / ".codegraph" / "config.json").read_text(encoding="utf-8")) == {"custom": True}


def test_onboard_rejects_missing_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    rc = cmd_onboard(_args("p", tmp_path / "nope"))
    assert rc == 1  # repo 不存在 → 关键步失败即停


def test_onboard_rejects_bad_project_id(tmp_path):
    rc = cmd_onboard(_args("Bad ID!", tmp_path))
    assert rc == 1  # 非法 project_id


def test_onboard_wires_sync_resources(tmp_path, monkeypatch):
    """sync 软步: onboard 调 sync_resources_to(repo)(接线钉死, spy 避免真拷大量资源)。"""
    repo = tmp_path / "r"
    repo.mkdir()
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", tmp_path / "meta")
    called: dict = {}
    import codev_platform.cli_cmds.sync as sync_mod
    monkeypatch.setattr(sync_mod, "sync_resources_to",
                        lambda r, **kw: called.setdefault("repo", r) is None)
    rc = cmd_onboard(_args("p", repo))
    assert rc == 0 and called["repo"] == repo.resolve()


def test_onboard_generates_mcp_json(tmp_path, monkeypatch):
    """.mcp.json 软步: onboard 经 build_mcp_servers 写 <repo>/.mcp.json。"""
    repo = tmp_path / "r"
    repo.mkdir()
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", tmp_path / "meta")
    import codev_platform.mcp_serve as ms
    monkeypatch.setattr(ms, "build_mcp_servers",
                        lambda cfg, target, pid: {"graph": {"type": "sse", "url": f"u/{pid}/{target}"}})
    rc = cmd_onboard(_args("p", repo))
    assert rc == 0
    data = json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["graph"]["url"] == "u/p/platform"


def test_onboard_preserves_existing_mcp_json(tmp_path, monkeypatch):
    """已有 .mcp.json 不覆盖(保用户自定义/token header)。"""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / ".mcp.json").write_text('{"mcpServers": {"custom": 1}}', encoding="utf-8")
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", tmp_path / "meta")
    rc = cmd_onboard(_args("p", repo))
    assert rc == 0
    assert json.loads((repo / ".mcp.json").read_text(encoding="utf-8")) == {"mcpServers": {"custom": 1}}


def test_onboard_custom_name_and_org(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    meta_root = tmp_path / "meta"
    cfg_store: dict = {"projects": {}}
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg_store)
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", meta_root)

    rc = cmd_onboard(_args("proj2", repo, org="acme", name="My Project"))
    assert rc == 0
    assert cfg_store["projects"]["proj2"]["org_id"] == "acme"
    meta = json.loads((meta_root / "proj2" / "meta.json").read_text(encoding="utf-8"))
    assert meta["display_name"] == "My Project"


def test_onboard_warns_when_sync_incomplete(tmp_path, monkeypatch, capsys):
    # sync 返 False(源缺失)→ onboard 显式 WARN + footer 不再无条件建议提交未同步的 rules(审计 B2 修复)。
    repo = tmp_path / "r"
    repo.mkdir()
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", tmp_path / "meta")
    import codev_platform.cli_cmds.sync as sync_mod
    monkeypatch.setattr(sync_mod, "sync_resources_to", lambda r, **kw: False)
    rc = cmd_onboard(_args("p", repo))
    out = capsys.readouterr().out
    assert rc == 0
    assert "未同步" in out                                              # 显式警告(不再静默报全成功)
    assert "project.json,rules,skills,hooks,settings.json" not in out   # footer 不用"全成功"版


def test_onboard_enqueues_onboard_meta(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    enq: list[tuple[str, str, JobMeta | None]] = []
    queue_options: list[dict[str, object]] = []
    target_commit = "b" * 40

    class _Q:
        def enqueue(self, pid, kind, meta=None):
            enq.append((pid, kind, meta))

    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", tmp_path / "meta")

    def _open_queue(**kwargs):
        queue_options.append(kwargs)
        return _Q()

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", _open_queue)
    monkeypatch.setattr("codev_platform.reindex.target_commit.resolve_repo_head", lambda _repo: target_commit)

    rc = cmd_onboard(_args("p", repo, no_index=False))

    assert rc == 0
    assert [kind for _pid, kind, _meta in enq] == list(REQUIRED_INDEX_KINDS)
    assert all(pid == "p" for pid, _kind, _meta in enq)
    assert all(
        meta == JobMeta(source="onboard", pull_policy="never", target_commit=target_commit)
        for _pid, _kind, meta in enq
    )
    assert queue_options == [{"fail_soft": False}]
