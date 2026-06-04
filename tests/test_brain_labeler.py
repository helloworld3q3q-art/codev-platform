"""BrainDomainLabeler(A1-2b)—— mock provider 测渲染/解析/越界/串台/fail-soft。

LLM 用 _FakeProvider 注入(固定返回文本), 不碰真 brain/key。真实 ≥70% 验收是 A1-3
(需真模型 + 人工核对), 本文件只钉确定性的 prompt 渲染 + 输出解析 + grounding 解析层。
"""
from __future__ import annotations

from codev_platform.agent.brain.base import LLMProvider
from codev_platform.agent.brain.types import AssistantTurn
from codev_platform.graph.analyzers.brain_labeler import BrainDomainLabeler
from codev_platform.graph.analyzers.domain_labeler import ClusterMember, ClusterRequest


class _FakeProvider(LLMProvider):
    name = "fake"
    model = "fake-model"

    def __init__(self, text):
        self._text = text
        self.last_prompt = None

    def chat(self, system, messages, tools):
        self.last_prompt = messages[0].content
        return AssistantTurn(text=self._text, tool_calls=[], stop_reason="end")


def _req(cid="root1"):
    return ClusterRequest(cluster_id=cid, members=(
        ClusterMember("e1", "endpoint", "GET /orders"),
        ClusterMember("t1", "table", "orders"),
    ))


def test_parse_plain_json():
    prov = _FakeProvider('[{"cluster":"c1","domain":"订单","members":["e1"]}]')
    labels = BrainDomainLabeler(provider=prov).label([_req("root1")])
    assert len(labels) == 1
    assert labels[0].cluster_id == "root1"      # 短号 c1 对齐回真 cluster_id
    assert labels[0].domain == "订单"
    assert labels[0].member_refs == ("e1",)


def test_parse_json_in_code_fence():
    prov = _FakeProvider('好的\n```json\n[{"cluster":"c1","domain":"行情","members":["e1"]}]\n```')
    assert BrainDomainLabeler(provider=prov).label([_req()])[0].domain == "行情"


def test_member_ref_out_of_range_dropped():
    # 模型回越界 ref "e9"(不在 cluster)→ 剔除(grounding 解析层)。
    prov = _FakeProvider('[{"cluster":"c1","domain":"订单","members":["e1","e9"]}]')
    assert BrainDomainLabeler(provider=prov).label([_req()])[0].member_refs == ("e1",)


def test_unknown_cluster_short_dropped():
    # 模型回不存在的 cluster 短号 c9 → 丢; 真 cluster 漏标 → None(fail-soft)。
    prov = _FakeProvider('[{"cluster":"c9","domain":"乱","members":[]}]')
    labels = BrainDomainLabeler(provider=prov).label([_req("root1")])
    assert len(labels) == 1 and labels[0].cluster_id == "root1" and labels[0].domain is None


def test_unparseable_text_fail_soft():
    labels = BrainDomainLabeler(provider=_FakeProvider("我无法理解")).label([_req("root1")])
    assert labels[0].domain is None             # 解析失败 → None, 不抛


def test_chat_error_fail_soft():
    class _Boom(LLMProvider):
        name = "boom"
        model = "m"

        def chat(self, system, messages, tools):
            raise RuntimeError("boom")

    assert BrainDomainLabeler(provider=_Boom()).label([_req("root1")])[0].domain is None


def test_non_string_domain_becomes_none():
    prov = _FakeProvider('[{"cluster":"c1","domain":123,"members":["e1"]}]')
    assert BrainDomainLabeler(provider=prov).label([_req()])[0].domain is None


def test_signature_includes_model_and_version():
    sig = BrainDomainLabeler(provider=_FakeProvider("[]")).signature
    assert "fake-model" in sig and "v1" in sig


def test_available_with_injected_provider():
    assert BrainDomainLabeler(provider=_FakeProvider("[]")).available() is True


def test_prompt_contains_closed_world_clauses():
    prov = _FakeProvider("[]")
    BrainDomainLabeler(provider=prov).label([_req()])
    assert "e1 (endpoint): GET /orders" in prov.last_prompt
    assert "t1 (table): orders" in prov.last_prompt


def test_multi_cluster_batch_aligns_back():
    prov = _FakeProvider('[{"cluster":"c1","domain":"订单","members":["e1"]},'
                         '{"cluster":"c2","domain":"行情","members":["e1"]}]')
    labels = BrainDomainLabeler(provider=prov).label([_req("rootA"), _req("rootB")])
    assert {lab.cluster_id: lab.domain for lab in labels} == {"rootA": "订单", "rootB": "行情"}
