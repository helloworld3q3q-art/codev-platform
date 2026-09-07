"""Pg 队列 schema 与状态转换 SQL 的单一真值。"""
from __future__ import annotations

NOW_SQL = "EXTRACT(EPOCH FROM clock_timestamp())"
TOKEN_SQL = "md5(random()::text || clock_timestamp()::text)"
_FLOAT_EPSILON = "2.220446049250313e-16::double precision"
_MIN_TAIL_STEP = "0.000001::double precision"

QUARANTINE_COLUMNS = (
    "quarantine_reason",
    "quarantine_attempt_id",
    "quarantine_fence",
    "quarantine_process_identity",
    "quarantine_containment_kind",
    "quarantine_native_ref",
    "quarantine_at",
)

QUEUE_COLUMN_CONTRACT = (
    ("project_id", "text", False),
    ("kind", "text", False),
    ("enqueued_at", "double precision", False),
    ("status", "text", False),
    ("claimed_by", "text", True),
    ("lease_expires_at", "double precision", True),
    ("claim_token", "text", True),
    ("pending_token", "text", True),
    ("pending_enqueued_at", "double precision", True),
    ("pending_updated_at", "double precision", True),
    ("pending_meta_json", "text", True),
    ("active_enqueued_at", "double precision", True),
    ("active_meta_json", "text", True),
    ("active_heartbeat_at", "double precision", True),
    ("result_status", "text", True),
    ("result_finished_at", "double precision", True),
    ("result_enqueued_at", "double precision", True),
    ("result_meta_json", "text", True),
    ("quarantine_reason", "text", True),
    ("quarantine_attempt_id", "text", True),
    ("quarantine_fence", "text", True),
    ("quarantine_process_identity", "text", True),
    ("quarantine_containment_kind", "text", True),
    ("quarantine_native_ref", "text", True),
    ("quarantine_at", "double precision", True),
)
QUEUE_PRIMARY_KEY = ("project_id", "kind")
QUEUE_CLAIM_INDEX_COLUMNS = ("status", "enqueued_at")

_CREATE_COLUMN_SQL = (
    "project_id TEXT NOT NULL",
    "kind TEXT NOT NULL",
    "enqueued_at DOUBLE PRECISION NOT NULL",
    "status TEXT NOT NULL DEFAULT 'pending'",
    "claimed_by TEXT",
    "lease_expires_at DOUBLE PRECISION",
    "claim_token TEXT",
    "pending_token TEXT",
    "pending_enqueued_at DOUBLE PRECISION",
    "pending_updated_at DOUBLE PRECISION",
    "pending_meta_json TEXT",
    "active_enqueued_at DOUBLE PRECISION",
    "active_meta_json TEXT",
    "active_heartbeat_at DOUBLE PRECISION",
    "result_status TEXT",
    "result_finished_at DOUBLE PRECISION",
    "result_enqueued_at DOUBLE PRECISION",
    "result_meta_json TEXT",
    "quarantine_reason TEXT",
    "quarantine_attempt_id TEXT",
    "quarantine_fence TEXT",
    "quarantine_process_identity TEXT",
    "quarantine_containment_kind TEXT",
    "quarantine_native_ref TEXT",
    "quarantine_at DOUBLE PRECISION",
)
_ALTER_COLUMN_SQL = _CREATE_COLUMN_SQL[6:]


def create_table_sql(table: str) -> str:
    columns = ",\n        ".join(_CREATE_COLUMN_SQL)
    primary_key = ", ".join(QUEUE_PRIMARY_KEY)
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        f"        {columns},\n"
        f"        PRIMARY KEY ({primary_key}))"
    )


def alter_table_sql(table: str) -> str:
    additions = ", ".join(
        f"ADD COLUMN IF NOT EXISTS {column}" for column in _ALTER_COLUMN_SQL
    )
    return f"ALTER TABLE {table} {additions}"


def create_index_sql(table: str) -> str:
    return f"CREATE INDEX IF NOT EXISTS ix_{table}_claim ON {table} (status, enqueued_at)"


def enqueue_sql(table: str) -> str:
    return f"""INSERT INTO {table} (
        project_id, kind, enqueued_at, status,
        pending_token, pending_enqueued_at, pending_updated_at, pending_meta_json
    ) VALUES (%s, %s, {NOW_SQL}, 'pending', {TOKEN_SQL}, {NOW_SQL}, {NOW_SQL}, %s)
    ON CONFLICT (project_id, kind) DO UPDATE SET
        enqueued_at = {NOW_SQL},
        status = CASE WHEN {table}.quarantine_at IS NULL THEN 'pending' ELSE {table}.status END,
        pending_token = {TOKEN_SQL}, pending_enqueued_at = {NOW_SQL},
        pending_updated_at = {NOW_SQL}, pending_meta_json = EXCLUDED.pending_meta_json"""


def claim_sql(table: str, *, project_filter: bool, limited: bool) -> str:
    projects = " AND project_id = ANY(%s)" if project_filter else ""
    limit = " LIMIT %s" if limited else ""
    return f"""WITH candidate AS (
        SELECT project_id, kind,
               COALESCE(pending_enqueued_at, active_enqueued_at, enqueued_at) AS next_enqueued_at,
               COALESCE(pending_meta_json, active_meta_json) AS next_meta_json
        FROM {table}
        WHERE quarantine_at IS NULL AND (
            ((pending_enqueued_at IS NOT NULL OR
              (pending_enqueued_at IS NULL AND status = 'pending' AND claim_token IS NULL))
             AND (claim_token IS NULL OR lease_expires_at < {NOW_SQL}))
            OR (pending_enqueued_at IS NULL AND claim_token IS NOT NULL
                AND lease_expires_at < {NOW_SQL})
        ){projects}
        ORDER BY COALESCE(pending_enqueued_at, active_enqueued_at, enqueued_at)
        {limit} FOR UPDATE SKIP LOCKED
    ) UPDATE {table} AS q SET
        status = 'running', claimed_by = %s,
        lease_expires_at = {NOW_SQL} + %s, claim_token = {TOKEN_SQL},
        active_enqueued_at = candidate.next_enqueued_at,
        active_meta_json = candidate.next_meta_json, active_heartbeat_at = {NOW_SQL},
        pending_token = NULL, pending_enqueued_at = NULL,
        pending_updated_at = NULL, pending_meta_json = NULL
    FROM candidate
    WHERE q.project_id = candidate.project_id AND q.kind = candidate.kind
    RETURNING q.project_id, q.kind, q.active_enqueued_at, q.claim_token,
              q.claimed_by, q.lease_expires_at, q.active_meta_json"""


def scan_sql(table: str, *, snapshot: bool) -> str:
    predicate = (
        "quarantine_at IS NOT NULL OR status IN ('pending','running','completed','discarded','failed')"
        if snapshot
        else "quarantine_at IS NULL AND (pending_enqueued_at IS NOT NULL OR "
             "(pending_enqueued_at IS NULL AND status = 'pending' AND claim_token IS NULL) OR "
             f"(pending_enqueued_at IS NULL AND claim_token IS NOT NULL AND lease_expires_at < {NOW_SQL}))"
    )
    return f"""SELECT project_id, kind, enqueued_at,
        pending_enqueued_at, pending_meta_json, status,
        active_enqueued_at, active_meta_json, claim_token, lease_expires_at,
        result_status, result_enqueued_at, result_meta_json, {NOW_SQL} AS now_ts,
        claimed_by, quarantine_reason, quarantine_attempt_id, quarantine_fence,
        quarantine_process_identity, quarantine_containment_kind,
        quarantine_native_ref, quarantine_at, pending_token, xmin::text AS pending_xmin
    FROM {table} WHERE {predicate}
    ORDER BY COALESCE(pending_enqueued_at, active_enqueued_at, result_enqueued_at, enqueued_at)"""


def pending_migration_read_sql(table: str) -> str:
    """锁定单行并读取 pending CAS 所需的最小状态。"""
    return f"""SELECT enqueued_at, pending_enqueued_at, pending_meta_json,
        status, claim_token, claimed_by, pending_token, quarantine_at, xmin::text
    FROM {table}
    WHERE project_id = %s AND kind = %s FOR UPDATE"""


def migrate_pending_token_sql(table: str) -> str:
    """以 v2 pending token 精确替换 pending 元数据。"""
    return f"""UPDATE {table} SET
        pending_token = {TOKEN_SQL},
        pending_enqueued_at = COALESCE(pending_enqueued_at, enqueued_at),
        pending_updated_at = {NOW_SQL},
        pending_meta_json = %s
    WHERE project_id = %s AND kind = %s AND quarantine_at IS NULL
      AND pending_enqueued_at IS NOT NULL AND pending_token = %s"""


def migrate_pending_xmin_sql(table: str) -> str:
    """以 legacy 行版本精确替换未带 pending token 的元数据。"""
    return f"""UPDATE {table} SET
        pending_token = {TOKEN_SQL},
        pending_enqueued_at = COALESCE(pending_enqueued_at, enqueued_at),
        pending_updated_at = {NOW_SQL},
        pending_meta_json = %s
    WHERE project_id = %s AND kind = %s AND quarantine_at IS NULL
      AND pending_token IS NULL AND xmin::text = %s
      AND pending_enqueued_at IS NULL AND status = 'pending'
      AND claim_token IS NULL AND claimed_by IS NULL AND pending_meta_json IS NULL"""


def recover_owned_sql(table: str) -> str:
    return f"""SELECT project_id, kind, COALESCE(active_enqueued_at, enqueued_at),
        claim_token, claimed_by, lease_expires_at, active_meta_json
    FROM {table}
    WHERE quarantine_at IS NULL AND claim_token IS NOT NULL AND claimed_by = %s
    ORDER BY COALESCE(active_enqueued_at, enqueued_at), project_id, kind"""


def dependency_state_sql(table: str) -> str:
    """读取单个依赖 key 的 pending/active/quarantine 事实。"""
    return f"""SELECT pending_meta_json, active_meta_json, claim_token, quarantine_at
    FROM {table} WHERE project_id = %s AND kind = %s"""


def renew_sql(table: str) -> str:
    return f"""UPDATE {table} SET lease_expires_at = {NOW_SQL} + %s,
        active_heartbeat_at = {NOW_SQL}
    WHERE project_id = %s AND kind = %s AND claim_token = %s AND claimed_by = %s
      AND quarantine_at IS NULL"""


def tail_lock_sql(table: str) -> str:
    """串行化会影响全局 pending 排序的写事务。"""
    return f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE"


def retry_sql(
    table: str,
    *,
    require_owner: bool = True,
    require_expired: bool = False,
    require_exact_lease: bool = False,
) -> str:
    owner = " AND claimed_by = %s" if require_owner else ""
    exact_lease = " AND lease_expires_at = %s" if require_exact_lease else ""
    expired = f" AND lease_expires_at < {NOW_SQL}" if require_expired else ""
    return f"""WITH matched AS (
        SELECT project_id, kind, pending_enqueued_at,
               pending_meta_json, active_meta_json
        FROM {table}
        WHERE project_id = %s AND kind = %s AND claim_token = %s{owner}{exact_lease}{expired}
          AND quarantine_at IS NULL FOR UPDATE
    ), tail_base AS (
        SELECT GREATEST(
            ({NOW_SQL})::double precision,
            COALESCE(MAX(pending_enqueued_at), '-Infinity'::double precision)
        ) AS tail_base
        FROM {table}
        WHERE quarantine_at IS NULL AND pending_enqueued_at IS NOT NULL
    ), tail AS (
        SELECT tail_base,
               tail_base + GREATEST(
                   {_MIN_TAIL_STEP}, ABS(tail_base) * {_FLOAT_EPSILON}
               ) AS tail_value
        FROM tail_base
    ), valid_tail AS (
        SELECT tail_value FROM tail
        WHERE tail_value > tail_base
          AND tail_value < 'Infinity'::double precision
    ) UPDATE {table} AS q SET status = 'pending',
        pending_token = {TOKEN_SQL},
        pending_enqueued_at = valid_tail.tail_value,
        pending_updated_at = {NOW_SQL},
        pending_meta_json = CASE WHEN matched.pending_enqueued_at IS NULL
                                 THEN matched.active_meta_json ELSE matched.pending_meta_json END,
        claimed_by = NULL, lease_expires_at = NULL, claim_token = NULL,
        active_enqueued_at = NULL, active_meta_json = NULL, active_heartbeat_at = NULL
    FROM matched CROSS JOIN valid_tail
    WHERE q.project_id = matched.project_id AND q.kind = matched.kind
    RETURNING q.project_id"""


def reject_sql(
    table: str,
    *,
    require_expired: bool = False,
    require_exact_lease: bool = False,
) -> str:
    exact_lease = " AND lease_expires_at = %s" if require_exact_lease else ""
    expired = f" AND lease_expires_at < {NOW_SQL}" if require_expired else ""
    return f"""WITH matched AS (
        SELECT project_id, kind, pending_enqueued_at,
               COALESCE(active_enqueued_at, enqueued_at) AS active_enqueued_at,
               active_meta_json, %s::text AS failure_reason
        FROM {table}
        WHERE project_id = %s AND kind = %s AND claim_token = %s AND claimed_by = %s
          AND quarantine_at IS NULL{exact_lease}{expired} FOR UPDATE
    ) UPDATE {table} AS q SET
        status = CASE WHEN matched.pending_enqueued_at IS NULL THEN 'failed' ELSE 'pending' END,
        claimed_by = NULL, lease_expires_at = NULL, claim_token = NULL,
        active_enqueued_at = NULL, active_meta_json = NULL, active_heartbeat_at = NULL,
        result_status = 'failed',
        result_finished_at = {NOW_SQL},
        result_enqueued_at = matched.active_enqueued_at,
        result_meta_json = (
            COALESCE(matched.active_meta_json, '{{}}')::jsonb
            || jsonb_build_object('failure_reason', matched.failure_reason)
        )::text
    FROM matched WHERE q.project_id = matched.project_id AND q.kind = matched.kind
    RETURNING q.project_id"""


def reject_unowned_legacy_active_sql(
    table: str,
    *,
    require_exact_lease: bool = False,
) -> str:
    """仅拒绝 owner 缺失且 claim token 精确匹配的旧 active。"""
    exact_lease = " AND lease_expires_at = %s" if require_exact_lease else ""
    return f"""WITH matched AS (
        SELECT project_id, kind, pending_enqueued_at,
               COALESCE(active_enqueued_at, enqueued_at) AS active_enqueued_at,
               active_meta_json, %s::text AS failure_reason
        FROM {table}
        WHERE project_id = %s AND kind = %s AND claim_token = %s
          AND claimed_by IS NULL AND status = 'running' AND quarantine_at IS NULL
          {exact_lease} AND lease_expires_at < {NOW_SQL} FOR UPDATE
    ) UPDATE {table} AS q SET
        status = CASE WHEN matched.pending_enqueued_at IS NULL THEN 'failed' ELSE 'pending' END,
        claimed_by = NULL, lease_expires_at = NULL, claim_token = NULL,
        active_enqueued_at = NULL, active_meta_json = NULL, active_heartbeat_at = NULL,
        result_status = 'failed',
        result_finished_at = {NOW_SQL},
        result_enqueued_at = matched.active_enqueued_at,
        result_meta_json = (
            COALESCE(matched.active_meta_json, '{{}}')::jsonb
            || jsonb_build_object('failure_reason', matched.failure_reason)
        )::text
    FROM matched WHERE q.project_id = matched.project_id AND q.kind = matched.kind
    RETURNING q.project_id"""


def quarantine_sql(table: str) -> str:
    same = " AND ".join((
        "quarantine_attempt_id = %s", "quarantine_fence = %s",
        "quarantine_process_identity = %s", "quarantine_containment_kind = %s",
        "quarantine_native_ref = %s", "quarantine_reason = %s",
    ))
    return f"""UPDATE {table} SET status = 'quarantined',
        quarantine_attempt_id = %s, quarantine_fence = %s,
        quarantine_process_identity = %s, quarantine_containment_kind = %s,
        quarantine_native_ref = %s, quarantine_reason = %s,
        quarantine_at = COALESCE(quarantine_at, {NOW_SQL})
    WHERE project_id = %s AND kind = %s AND claim_token = %s AND claimed_by = %s
      AND (quarantine_at IS NULL OR ({same}))
    RETURNING project_id, kind, claim_token, quarantine_attempt_id, quarantine_fence,
        quarantine_process_identity, quarantine_containment_kind,
        quarantine_native_ref, quarantine_reason, quarantine_at"""


def publish_lock_sql(table: str) -> str:
    return f"""SELECT pending_enqueued_at, pending_meta_json, active_meta_json
    FROM {table}
    WHERE project_id = %s AND kind = %s AND claim_token = %s AND claimed_by = %s
      AND quarantine_at IS NULL FOR UPDATE"""


def publish_ack_sql(table: str) -> str:
    return f"""UPDATE {table} SET
        status = CASE WHEN pending_enqueued_at IS NULL THEN 'completed' ELSE 'pending' END,
        claimed_by = NULL, lease_expires_at = NULL, claim_token = NULL,
        active_enqueued_at = NULL, active_meta_json = NULL, active_heartbeat_at = NULL,
        result_status = %s, result_finished_at = {NOW_SQL},
        result_enqueued_at = COALESCE(active_enqueued_at, enqueued_at),
        result_meta_json = active_meta_json
    WHERE project_id = %s AND kind = %s AND claim_token = %s AND claimed_by = %s
      AND quarantine_at IS NULL
    RETURNING pending_enqueued_at"""


def clear_quarantine_sql(table: str) -> str:
    return f"""UPDATE {table} SET status = 'pending',
        pending_token = CASE WHEN pending_enqueued_at IS NULL THEN {TOKEN_SQL}
                             ELSE pending_token END,
        pending_enqueued_at = COALESCE(pending_enqueued_at, active_enqueued_at, enqueued_at),
        pending_updated_at = CASE WHEN pending_enqueued_at IS NULL THEN {NOW_SQL}
                                  ELSE pending_updated_at END,
        pending_meta_json = CASE WHEN pending_enqueued_at IS NULL
                                 THEN active_meta_json ELSE pending_meta_json END,
        claimed_by = NULL, lease_expires_at = NULL, claim_token = NULL,
        active_enqueued_at = NULL, active_meta_json = NULL, active_heartbeat_at = NULL,
        quarantine_reason = NULL, quarantine_attempt_id = NULL, quarantine_fence = NULL,
        quarantine_process_identity = NULL, quarantine_containment_kind = NULL,
        quarantine_native_ref = NULL, quarantine_at = NULL
    WHERE project_id = %s AND kind = %s AND claim_token = %s
      AND quarantine_attempt_id = %s AND quarantine_fence = %s
      AND quarantine_process_identity = %s AND quarantine_containment_kind = %s
      AND quarantine_native_ref = %s AND quarantine_reason = %s AND quarantine_at = %s
      AND quarantine_at IS NOT NULL"""


def discard_sql(table: str) -> str:
    return f"""WITH matched AS (
        SELECT project_id, kind, pending_enqueued_at, pending_meta_json, claim_token,
               status, enqueued_at FROM {table}
        WHERE project_id = %s AND kind = %s AND quarantine_at IS NULL FOR UPDATE
    ) UPDATE {table} AS q SET
        status = CASE WHEN q.claim_token IS NOT NULL THEN 'running' ELSE 'discarded' END,
        pending_token = NULL, pending_enqueued_at = NULL,
        pending_updated_at = NULL, pending_meta_json = NULL,
        result_status = 'discarded', result_finished_at = {NOW_SQL},
        result_enqueued_at = COALESCE(matched.pending_enqueued_at, matched.enqueued_at),
        result_meta_json = matched.pending_meta_json
    FROM matched WHERE q.project_id = matched.project_id AND q.kind = matched.kind
      AND (matched.pending_enqueued_at IS NOT NULL OR
           (matched.pending_enqueued_at IS NULL AND matched.status = 'pending'
            AND matched.claim_token IS NULL))
      AND COALESCE(matched.pending_enqueued_at, matched.enqueued_at) <= %s
    RETURNING q.project_id"""


def reclaim_owned_sql(table: str) -> str:
    return f"""WITH matched AS (
        SELECT project_id, kind, pending_enqueued_at,
               COALESCE(active_enqueued_at, enqueued_at) AS active_enqueued_at,
               active_meta_json
        FROM {table}
        WHERE claim_token IS NOT NULL AND claimed_by = %s
          AND quarantine_at IS NULL FOR UPDATE
    ) UPDATE {table} AS q SET status = 'pending',
        pending_token = CASE WHEN matched.pending_enqueued_at IS NULL THEN {TOKEN_SQL}
                             ELSE q.pending_token END,
        pending_enqueued_at = CASE WHEN matched.pending_enqueued_at IS NULL
                                   THEN matched.active_enqueued_at ELSE q.pending_enqueued_at END,
        pending_updated_at = CASE WHEN matched.pending_enqueued_at IS NULL THEN {NOW_SQL}
                                  ELSE q.pending_updated_at END,
        pending_meta_json = CASE WHEN matched.pending_enqueued_at IS NULL
                                 THEN matched.active_meta_json ELSE q.pending_meta_json END,
        claimed_by = NULL, lease_expires_at = NULL, claim_token = NULL,
        active_enqueued_at = NULL, active_meta_json = NULL, active_heartbeat_at = NULL
    FROM matched WHERE q.project_id = matched.project_id AND q.kind = matched.kind
    RETURNING q.project_id"""


__all__ = [
    "QUARANTINE_COLUMNS", "QUEUE_CLAIM_INDEX_COLUMNS", "QUEUE_COLUMN_CONTRACT",
    "QUEUE_PRIMARY_KEY", "alter_table_sql", "claim_sql", "clear_quarantine_sql",
    "create_index_sql", "create_table_sql", "dependency_state_sql", "discard_sql",
    "enqueue_sql", "migrate_pending_token_sql", "migrate_pending_xmin_sql",
    "pending_migration_read_sql", "publish_ack_sql", "publish_lock_sql", "quarantine_sql",
    "reclaim_owned_sql", "recover_owned_sql", "reject_sql",
    "reject_unowned_legacy_active_sql", "renew_sql",
    "retry_sql", "scan_sql", "tail_lock_sql",
]
