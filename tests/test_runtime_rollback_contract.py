"""基线观察、完整回滚包与对象绑定契约测试。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from codev_platform.core.runtime_models import canonical_json_bytes
from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNIT_NAMES
from codev_platform.runtime_rollback_contract import (
    BaselineObservation,
    PayloadCategory,
    PayloadLogicalRole,
    RollbackBundle,
    RollbackContractError,
    baseline_observation_sha256,
    create_rollback_bundle,
    decode_baseline_observation,
    decode_protected_payload_ref,
    decode_rollback_bundle,
    encode_baseline_observation,
    encode_protected_payload_ref,
    encode_rollback_bundle,
    rollback_bundle_sha256,
    verify_rollback_bundle,
)
from tests.runtime_rollback_test_support import (
    ATTEMPT_ID,
    boundary_payload_groups,
    bound_bundle,
    bound_facts,
    bound_facts_for_payloads,
    oversized_payload_groups,
    protected_ref,
    protected_groups,
    rollback_bundle_candidate_mapping,
)
from tests.runtime_contract_malicious_support import strict_json_mutations


def test_observation与bundle模型冻结且绑定字段精确() -> None:
    assert tuple(field.name for field in dataclasses.fields(BaselineObservation)) == (
        "schema_version",
        "serving_generation_id",
        "generation_state_sha256",
        "generation_acceptance_sha256",
        "serve_permit_sha256",
        "main_pid",
        "interpreter_identity_sha256",
        "systemd_state_sha256",
        "configuration_state_sha256",
        "database_state_sha256",
        "index_set_sha256",
        "observed_at",
    )
    assert tuple(field.name for field in dataclasses.fields(RollbackBundle)) == (
        "schema_version",
        "attempt_id",
        "plan_sha256",
        "baseline_observation_sha256",
        "original_serving_generation_id",
        "systemd_payloads",
        "configuration_payloads",
        "receipt_payloads",
        "database_compatibility_sha256",
        "index_set_sha256",
        "created_at",
    )
    for model in (BaselineObservation, RollbackBundle):
        assert model.__dataclass_params__.frozen is True
        assert "__dict__" not in model.__slots__


def test_observation摘要排除时间但绑定全部现场事实() -> None:
    observation = bound_facts().observation
    later = dataclasses.replace(observation, observed_at="2026-07-19T11:00:00Z")

    assert baseline_observation_sha256(observation) == baseline_observation_sha256(later)
    for field, value in (
        ("serving_generation_id", "b" * 64),
        ("generation_state_sha256", "c" * 64),
        ("generation_acceptance_sha256", "d" * 64),
        ("serve_permit_sha256", "e" * 64),
        ("main_pid", 5678),
        ("interpreter_identity_sha256", "f" * 64),
        ("systemd_state_sha256", "a" * 64),
        ("configuration_state_sha256", "b" * 64),
        ("database_state_sha256", "c" * 64),
        ("index_set_sha256", "d" * 64),
    ):
        changed = dataclasses.replace(observation, **{field: value})
        assert baseline_observation_sha256(changed) != baseline_observation_sha256(
            observation
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"schema_version": True},
        {"schema_version": 2},
        {"serving_generation_id": "0" * 64},
        {"generation_state_sha256": "A" * 64},
        {"main_pid": True},
        {"main_pid": 0},
        {"main_pid": 2**31},
        {"observed_at": "2026-07-19T10:00:00+00:00"},
        {"observed_at": "2026-02-30T10:00:00Z"},
    ),
)
def test_observation非法字段fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(RollbackContractError):
        dataclasses.replace(bound_facts().observation, **changes)


def test_bundle_factory只从三类已验证对象派生全部绑定字段() -> None:
    facts = bound_facts()
    bundle = bound_bundle()

    assert bundle.attempt_id == facts.attempt.attempt_id
    assert bundle.plan_sha256 == facts.attempt.plan_sha256
    assert bundle.baseline_observation_sha256 == (
        facts.attempt.baseline_observation_sha256
    )
    assert bundle.original_serving_generation_id == facts.generation.generation_id
    assert bundle.original_serving_generation_id == facts.observation.serving_generation_id
    assert bundle.database_compatibility_sha256 == (
        facts.generation.database_contract_sha256
    )
    assert bundle.index_set_sha256 == facts.generation.index_set_sha256
    assert verify_rollback_bundle(
        bundle,
        facts.attempt,
        facts.generation,
        facts.observation,
    ) is bundle


def test_bundle_factory拒绝合法但超出decoder预算的聚合() -> None:
    systemd, configuration, receipts = oversized_payload_groups()
    facts = bound_facts_for_payloads(systemd, configuration, receipts)
    assert len(canonical_json_bytes(rollback_bundle_candidate_mapping(facts))) == 2_165_285

    with pytest.raises(RollbackContractError, match="序列化.*上限"):
        create_rollback_bundle(
            attempt=facts.attempt,
            baseline_generation=facts.generation,
            baseline_observation=facts.observation,
            systemd_payloads=systemd,
            configuration_payloads=configuration,
            receipt_payloads=receipts,
            created_at="2026-07-19T10:02:00Z",
        )


@pytest.mark.parametrize(
    ("extra_path_bytes", "expected_bytes", "accepted"),
    ((355, 1_048_576, True), (356, 1_048_577, False)),
)
def test_bundle_factory与decoder共享相邻canonical预算边界(
    extra_path_bytes: int,
    expected_bytes: int,
    accepted: bool,
) -> None:
    groups = boundary_payload_groups(extra_path_bytes)
    facts = bound_facts_for_payloads(*groups)
    assert len(canonical_json_bytes(rollback_bundle_candidate_mapping(facts))) == (
        expected_bytes
    )
    if not accepted:
        with pytest.raises(RollbackContractError, match="序列化.*上限"):
            create_rollback_bundle(
                attempt=facts.attempt,
                baseline_generation=facts.generation,
                baseline_observation=facts.observation,
                systemd_payloads=facts.systemd,
                configuration_payloads=facts.configuration,
                receipt_payloads=facts.receipts,
                created_at="2026-07-19T10:02:00Z",
            )
        return

    bundle = create_rollback_bundle(
        attempt=facts.attempt,
        baseline_generation=facts.generation,
        baseline_observation=facts.observation,
        systemd_payloads=facts.systemd,
        configuration_payloads=facts.configuration,
        receipt_payloads=facts.receipts,
        created_at="2026-07-19T10:02:00Z",
    )
    encoded = encode_rollback_bundle(bundle)
    assert len(encoded) == expected_bytes
    assert decode_rollback_bundle(encoded) == bundle


def test_real_registry多unit_bundle可往返并由verifier复证() -> None:
    services = (
        "codev-mcp-platform-docs.service",
        "codev-mcp-codegraph.service",
    )
    timers = (
        "codev-clock-resync.timer",
        "codev-memory-maintenance.timer",
    )
    assert set((*services, *timers)) <= MANAGED_SYSTEMD_UNIT_NAMES
    systemd = [
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_UNIT,
            unit,
            target_key=unit,
        )
        for unit in (*services, *timers)
    ]
    systemd.extend(
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_DROP_IN,
            f"drop-ins/{unit}.d/10-runtime.conf",
            target_key=f"{unit}@10-runtime.conf",
        )
        for unit in services
    )
    _, configuration, receipts = protected_groups()
    facts = bound_facts_for_payloads(
        tuple(sorted(systemd, key=lambda item: item.relative_path)),
        configuration,
        receipts,
    )
    bundle = create_rollback_bundle(
        attempt=facts.attempt,
        baseline_generation=facts.generation,
        baseline_observation=facts.observation,
        systemd_payloads=facts.systemd,
        configuration_payloads=facts.configuration,
        receipt_payloads=facts.receipts,
        created_at="2026-07-19T10:02:00Z",
    )

    decoded = decode_rollback_bundle(encode_rollback_bundle(bundle))

    assert len(decoded.systemd_payloads) == 6
    assert verify_rollback_bundle(
        decoded,
        facts.attempt,
        facts.generation,
        facts.observation,
    ) is decoded


@pytest.mark.parametrize(
    ("target", "field", "value", "message"),
    (
        ("attempt", "baseline_generation_id", "b" * 64, "generation"),
        ("attempt", "baseline_observation_sha256", "c" * 64, "observation"),
        ("observation", "serving_generation_id", "d" * 64, "serving"),
        ("observation", "systemd_state_sha256", "e" * 64, "systemd"),
        ("observation", "configuration_state_sha256", "f" * 64, "configuration"),
        ("observation", "index_set_sha256", "a" * 64, "index"),
    ),
)
def test_bundle_factory拒绝attempt_generation_observation事实漂移(
    target: str,
    field: str,
    value: str,
    message: str,
) -> None:
    facts = bound_facts()
    attempt = facts.attempt
    observation = facts.observation
    if target == "attempt":
        attempt = dataclasses.replace(attempt, **{field: value})
    else:
        observation = dataclasses.replace(observation, **{field: value})
        attempt = dataclasses.replace(
            attempt,
            baseline_observation_sha256=baseline_observation_sha256(observation),
        )

    with pytest.raises(RollbackContractError, match=message):
        create_rollback_bundle(
            attempt=attempt,
            baseline_generation=facts.generation,
            baseline_observation=observation,
            systemd_payloads=facts.systemd,
            configuration_payloads=facts.configuration,
            receipt_payloads=facts.receipts,
            created_at="2026-07-19T10:02:00Z",
        )


@pytest.mark.parametrize(
    "mutation",
    ("missing", "extra", "identity", "target-key", "logical-role"),
)
def test_bundle_factory拒绝权威manifest缺项多项与身份漂移(mutation: str) -> None:
    facts = bound_facts()
    systemd = facts.systemd
    receipts = facts.receipts
    if mutation == "missing":
        systemd = ()
    elif mutation == "extra":
        receipts = (
            *receipts,
            protected_ref(
                PayloadCategory.RECEIPT,
                PayloadLogicalRole.GENERATION_RECEIPT,
                "generation.json",
            ),
        )
    elif mutation == "identity":
        systemd = (dataclasses.replace(systemd[0], identity_sha256="c" * 64),)
    elif mutation == "target-key":
        systemd = (dataclasses.replace(systemd[0], target_key="other.service"),)
    else:
        systemd = (
            dataclasses.replace(
                systemd[0],
                logical_role=PayloadLogicalRole.SYSTEMD_DROP_IN,
            ),
        )

    with pytest.raises(RollbackContractError, match="manifest|不能为空"):
        create_rollback_bundle(
            attempt=facts.attempt,
            baseline_generation=facts.generation,
            baseline_observation=facts.observation,
            systemd_payloads=systemd,
            configuration_payloads=facts.configuration,
            receipt_payloads=receipts,
            created_at="2026-07-19T10:02:00Z",
        )


def test_bundle_factory拒绝错分类与其他attempt命名空间() -> None:
    facts = bound_facts()
    with pytest.raises(RollbackContractError, match="category"):
        create_rollback_bundle(
            attempt=facts.attempt,
            baseline_generation=facts.generation,
            baseline_observation=facts.observation,
            systemd_payloads=facts.receipts,
            configuration_payloads=facts.configuration,
            receipt_payloads=facts.systemd,
            created_at="2026-07-19T10:02:00Z",
        )
    other = dataclasses.replace(
        facts.systemd[0],
        relative_path=facts.systemd[0].relative_path.replace(ATTEMPT_ID, "2" * 32),
    )
    with pytest.raises(RollbackContractError, match="attempt"):
        create_rollback_bundle(
            attempt=facts.attempt,
            baseline_generation=facts.generation,
            baseline_observation=facts.observation,
            systemd_payloads=(other,),
            configuration_payloads=facts.configuration,
            receipt_payloads=facts.receipts,
            created_at="2026-07-19T10:02:00Z",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("plan_sha256", "b" * 64),
        ("baseline_observation_sha256", "c" * 64),
        ("original_serving_generation_id", "d" * 64),
        ("database_compatibility_sha256", "e" * 64),
        ("index_set_sha256", "f" * 64),
    ),
)
def test_bundle读取复验拒绝绑定字段漂移(field: str, value: str) -> None:
    facts = bound_facts()
    changed = dataclasses.replace(bound_bundle(), **{field: value})

    with pytest.raises(RollbackContractError, match="绑定"):
        verify_rollback_bundle(
            changed,
            facts.attempt,
            facts.generation,
            facts.observation,
        )


def test_bundle读取复验拒绝manifest多项与身份漂移() -> None:
    facts = bound_facts()
    bundle = bound_bundle()
    mutations = (
        dataclasses.replace(
            bundle,
            receipt_payloads=(
                *bundle.receipt_payloads,
                protected_ref(
                    PayloadCategory.RECEIPT,
                    PayloadLogicalRole.GENERATION_RECEIPT,
                    "generation.json",
                ),
            ),
        ),
        dataclasses.replace(
            bundle,
            configuration_payloads=(
                dataclasses.replace(
                    bundle.configuration_payloads[0],
                    identity_sha256="c" * 64,
                ),
            ),
        ),
    )
    for changed in mutations:
        with pytest.raises(RollbackContractError):
            verify_rollback_bundle(
                changed,
                facts.attempt,
                facts.generation,
                facts.observation,
            )


def test_bundle直接构造拒绝空分类未排序与超量tuple() -> None:
    bundle = bound_bundle()
    with pytest.raises(RollbackContractError, match="不能为空"):
        dataclasses.replace(bundle, systemd_payloads=())
    first = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "a.unit",
    )
    second = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_DROP_IN,
        "z.conf",
    )
    with pytest.raises(RollbackContractError, match="排序"):
        dataclasses.replace(bundle, systemd_payloads=(second, first))
    many = tuple(first for _ in range(257))
    with pytest.raises(RollbackContractError, match="数量"):
        dataclasses.replace(bundle, systemd_payloads=many)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("plan_sha256", "b" * 64),
        ("baseline_observation_sha256", "c" * 64),
        ("original_serving_generation_id", "d" * 64),
        ("database_compatibility_sha256", "e" * 64),
        ("index_set_sha256", "f" * 64),
    ),
)
def test_bundle摘要表驱动绑定每个标量身份字段(field: str, value: object) -> None:
    bundle = bound_bundle()
    changed = dataclasses.replace(bundle, **{field: value})
    assert rollback_bundle_sha256(changed) != rollback_bundle_sha256(bundle)


def test_bundle摘要固定canonical身份显式绑定schema与attempt() -> None:
    bundle = bound_bundle()

    assert bundle.schema_version == 1
    assert bundle.attempt_id == ATTEMPT_ID
    assert rollback_bundle_sha256(bundle) == (
        "437bcaf321f230d72c0a466634dfa384ae4fa2f235a2e2b350250572e199f2b7"
    )


@pytest.mark.parametrize(
    "field",
    ("systemd_payloads", "configuration_payloads", "receipt_payloads"),
)
def test_bundle摘要表驱动绑定每个载荷分类(field: str) -> None:
    bundle = bound_bundle()
    payloads = getattr(bundle, field)
    changed_payload = dataclasses.replace(payloads[0], identity_sha256="c" * 64)
    changed = dataclasses.replace(bundle, **{field: (changed_payload,)})
    assert rollback_bundle_sha256(changed) != rollback_bundle_sha256(bundle)


def test_bundle摘要唯一排除created_at() -> None:
    bundle = bound_bundle()
    later = dataclasses.replace(bundle, created_at="2026-07-19T11:00:00Z")
    assert rollback_bundle_sha256(later) == rollback_bundle_sha256(bundle)


def test_observation与bundle规范往返() -> None:
    observation = bound_facts().observation
    bundle = bound_bundle()
    assert decode_baseline_observation(encode_baseline_observation(observation)) == observation
    assert decode_rollback_bundle(encode_rollback_bundle(bundle)) == bundle


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        pytest.param(decoder, payload, id=f"{name}-{mutation}")
        for name, decoder, encoded, wrong_field in (
            (
                "protected-payload",
                decode_protected_payload_ref,
                encode_protected_payload_ref(bound_bundle().systemd_payloads[0]),
                "logical_role",
            ),
            (
                "observation",
                decode_baseline_observation,
                encode_baseline_observation(bound_facts().observation),
                "main_pid",
            ),
            (
                "bundle",
                decode_rollback_bundle,
                encode_rollback_bundle(bound_bundle()),
                "systemd_payloads",
            ),
        )
        for mutation, payload in strict_json_mutations(
            encoded,
            max_bytes=1_048_576,
            wrong_field=wrong_field,
        )
    ],
)
def test_rollback每个decoder逐项拒绝单变量恶意载荷(
    decoder,
    payload: object,
) -> None:
    with pytest.raises(RollbackContractError):
        decoder(payload)  # type: ignore[arg-type]


def test_bundle解码额外拒绝错误嵌套容器未知字段与超量数组() -> None:
    payload = json.loads(encode_rollback_bundle(bound_bundle()))
    payload["systemd_payloads"] = {}
    with pytest.raises(RollbackContractError):
        decode_rollback_bundle(json.dumps(payload).encode("utf-8"))
    payload = json.loads(encode_rollback_bundle(bound_bundle()))
    payload["systemd_payloads"][0]["destination"] = "/etc/systemd/system/service"
    with pytest.raises(RollbackContractError):
        decode_rollback_bundle(json.dumps(payload).encode("utf-8"))
    payload = json.loads(encode_rollback_bundle(bound_bundle()))
    payload["systemd_payloads"] *= 257
    with pytest.raises(RollbackContractError):
        decode_rollback_bundle(json.dumps(payload).encode("utf-8"))
