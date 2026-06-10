"""brain/ 适配器文件纪律: 适配器按协议族分文件, 不按厂商分。

闭合 agent-provider-architecture.md §6 自挂盲区:
  "缺: 静态扫描断言 brain/ 下适配器文件数 <= 协议族数 (防'每厂商一文件'复发)"

铁律 (见 .claude/rules/agent-provider-architecture.md §1): 市面 95% 模型走 OpenAI
兼容协议, 加一家厂商应是 config 一段 / registry 一行, 绝不新建厂商专属文件。
本测试在 brain/ 出现"非基础设施、非已知协议族"的新 .py 时失败, 把这条铁律变成机制。
"""
from __future__ import annotations

from pathlib import Path

import codev_platform.agent.brain as brain_pkg

_BRAIN_DIR = Path(brain_pkg.__file__).parent

# 基础设施文件 (策略接口 / 中性类型 / 注册表 / 包入口) —— 不是 provider 适配器。
_INFRA_FILES = {"__init__.py", "base.py", "types.py", "registry.py"}

# 协议族适配器: 一个文件 = 一个协议族, 一族吃掉该协议下所有厂商。
#   anthropic.py    = Anthropic 协议 (Claude)
#   openai_compat.py = OpenAI 兼容协议族 (GPT/DeepSeek/Qwen/Kimi/智谱/Groq/Ollama/vLLM ...)
# 新增条目的唯一正当理由: 接入一个全新协议 (非 OpenAI/Anthropic), 且要同步更新本集合 + 规则 §1/§2。
_PROTOCOL_FAMILY_ADAPTERS = {"anthropic.py", "openai_compat.py"}


def _adapter_files() -> set[str]:
    """brain/ 下既非基础设施、也非已知协议族适配器的 .py —— 疑似'每厂商一文件'复发。"""
    py = {p.name for p in _BRAIN_DIR.glob("*.py")}
    return py - _INFRA_FILES - _PROTOCOL_FAMILY_ADAPTERS


def test_no_per_vendor_adapter_file():
    stray = _adapter_files()
    assert not stray, (
        f"brain/ 出现未登记的适配器文件 {sorted(stray)}。"
        "按 agent-provider-architecture.md §1: 加 OpenAI 兼容厂商走 config/registry, 不新建文件。"
        "若确为'全新协议'适配器, 把文件名加进 _PROTOCOL_FAMILY_ADAPTERS 并更新规则。"
    )


def test_adapter_count_le_protocol_families():
    # 适配器文件总数 <= 协议族数 (当前 2: Anthropic + OpenAI 兼容)。
    adapters = {p.name for p in _BRAIN_DIR.glob("*.py")} - _INFRA_FILES
    assert len(adapters) <= len(_PROTOCOL_FAMILY_ADAPTERS), (
        f"适配器文件数 {len(adapters)} 超过协议族数 {len(_PROTOCOL_FAMILY_ADAPTERS)}: {sorted(adapters)}"
    )


def test_known_protocol_adapters_exist():
    # 防误删: 两个协议族适配器必须在位。
    py = {p.name for p in _BRAIN_DIR.glob("*.py")}
    for f in _PROTOCOL_FAMILY_ADAPTERS:
        assert f in py, f"协议族适配器缺失: {f}"
