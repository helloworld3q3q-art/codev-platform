"""builtin.sql Java JPA / dao-service QueryModel 扫描测试。"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import EdgeKind, NodeKind
from codev_platform.plugins.builtin.sql import SqlPlugin

PID = "demo"

_ENTITY = """\
package com.gillion.mappings.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;

@Entity
@Table(name = "unified_param_mapping_config")
public class UnifiedParamMappingConfig extends BaseModel {
    @Id
    @Column(name = "id")
    private Long id;

    @Column(name = "scene_code")
    private String sceneCode;

    @Column(name = "target_value")
    private String targetValue;
}
"""

_QUERYMODEL = """\
package com.gillion.mappings.model.querymodel;

public class QUnifiedParamMappingConfig extends BaseModelExpression<UnifiedParamMappingConfig, Long> {
    public static final BaseModelExpression<UnifiedParamMappingConfig, Long> unifiedParamMappingConfig =
            new QUnifiedParamMappingConfig();
    public static final FieldExpression<String> sceneCode =
            unifiedParamMappingConfig.fieldOf("sceneCode", String.class);
    public static final FieldExpression<String> targetValue =
            unifiedParamMappingConfig.fieldOf("targetValue", String.class);
}
"""

_SERVICE = """\
package com.gillion.mappings.service.impl;

public class ParamMappingConfigQueryService {
    public List<UnifiedParamMappingConfig> queryConfigs(ParamMappingQueryDTO query) {
        return QUnifiedParamMappingConfig.unifiedParamMappingConfig
                .select()
                .where(QUnifiedParamMappingConfig.sceneCode.eq$(query.getSceneCode()))
                .execute();
    }

    public String queryMappingValue(ParamMappingValueQueryDTO query) {
        return QUnifiedParamMappingConfig.unifiedParamMappingConfig
                .select(QUnifiedParamMappingConfig.targetValue)
                .where(QUnifiedParamMappingConfig.sceneCode.eq$(query.getSceneCode()))
                .execute()
                .getFirst()
                .getTargetValue();
    }

    private boolean checkMappingExistsInDb(String sceneCode) {
        int count = QUnifiedParamMappingConfig.unifiedParamMappingConfig
                .selectCount()
                .where(QUnifiedParamMappingConfig.sceneCode.eq$(sceneCode))
                .execute();
        return count > 0;
    }
}
"""

_ENTITY_WITH_NESTED_ANNOTATIONS = """\
package com.gillion.mappings.model;

@Entity
@Table(
    indexes = @Index(name = "idx_scene_code", columnList = "scene_code"),
    name = "unified_param_mapping_config"
)
public class UnifiedParamMappingConfig extends BaseModel {
    @Column(columnDefinition = "decimal(19,2)", name = "pay_amount")
    private BigDecimal payAmount;

    @Column
    private String fallbackName;
}
"""


def _write_repo(tmp_path: Path) -> None:
    (tmp_path / "UnifiedParamMappingConfig.java").write_text(_ENTITY, encoding="utf-8")
    (tmp_path / "QUnifiedParamMappingConfig.java").write_text(_QUERYMODEL, encoding="utf-8")
    (tmp_path / "ParamMappingConfigQueryService.java").write_text(_SERVICE, encoding="utf-8")


def _edges(result, kind: str) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in result.edges if e.kind == kind}


def test_java_jpa_detect(tmp_path: Path) -> None:
    (tmp_path / "UnifiedParamMappingConfig.java").write_text(_ENTITY, encoding="utf-8")
    assert SqlPlugin().detect(tmp_path) is True


def test_java_jpa_entity_extracts_table_and_columns(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    result = SqlPlugin().analyze(tmp_path, PID)

    tables = {n.name: n for n in result.nodes if n.kind == NodeKind.DB_TABLE.value}
    cols = {
        n.name
        for n in result.nodes
        if n.kind == NodeKind.DB_COLUMN.value and (n.meta or {}).get("table") == "unified_param_mapping_config"
    }

    assert "unified_param_mapping_config" in tables
    table = tables["unified_param_mapping_config"]
    assert table.language == "java"
    assert table.meta.get("source") == "java-jpa"
    assert table.meta.get("entity_class") == "UnifiedParamMappingConfig"
    assert {"id", "scene_code", "target_value"} <= cols


def test_dao_service_querymodel_reads_table(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    result = SqlPlugin().analyze(tmp_path, PID)

    target = f"{PID}:db_table:unified_param_mapping_config"
    reads = _edges(result, EdgeKind.READS_TABLE.value)
    assert (
        f"{PID}:backend_function:ParamMappingConfigQueryService.java:queryConfigs",
        target,
    ) in reads
    assert (
        f"{PID}:backend_function:ParamMappingConfigQueryService.java:queryMappingValue",
        target,
    ) in reads
    assert (
        f"{PID}:backend_function:ParamMappingConfigQueryService.java:checkMappingExistsInDb",
        target,
    ) in reads

    funcs = {n.name: n for n in result.nodes if n.kind == NodeKind.BACKEND_FUNCTION.value}
    assert funcs["queryConfigs"].language == "java"
    assert funcs["queryConfigs"].meta.get("dao_service") is True

    edge = next(
        e for e in result.edges
        if e.kind == EdgeKind.READS_TABLE.value
        and e.source.endswith(":queryConfigs")
        and e.target == target
    )
    assert edge.confidence == 0.9


def test_dao_service_querymodel_keeps_entity_mapping_when_sql_table_wins(
    tmp_path: Path,
) -> None:
    (tmp_path / "schema.sql").write_text(
        "CREATE TABLE unified_param_mapping_config (id BIGINT);",
        encoding="utf-8",
    )
    _write_repo(tmp_path)
    result = SqlPlugin().analyze(tmp_path, PID)

    target = f"{PID}:db_table:unified_param_mapping_config"
    reads = _edges(result, EdgeKind.READS_TABLE.value)
    assert (
        f"{PID}:backend_function:ParamMappingConfigQueryService.java:queryConfigs",
        target,
    ) in reads

    table = next(
        n for n in result.nodes
        if n.kind == NodeKind.DB_TABLE.value and n.id == target
    )
    assert table.language == "sql"
    assert table.meta.get("entity_class") is None


def test_java_jpa_reads_top_level_annotation_name(tmp_path: Path) -> None:
    (tmp_path / "UnifiedParamMappingConfig.java").write_text(
        _ENTITY_WITH_NESTED_ANNOTATIONS,
        encoding="utf-8",
    )
    result = SqlPlugin().analyze(tmp_path, PID)

    tables = {n.name: n for n in result.nodes if n.kind == NodeKind.DB_TABLE.value}
    cols = {
        n.name
        for n in result.nodes
        if n.kind == NodeKind.DB_COLUMN.value
    }

    assert "unified_param_mapping_config" in tables
    assert "idx_scene_code" not in tables
    assert {"pay_amount", "fallbackName"} <= cols
