"""Adversarial PG probe for PgJobQueue 多机健壮化 (commit e657e4a).
Isolated table reindex_jobs_haudit_<rand>, DROP at end. NEVER touches reindex_jobs.
"""
import os
import random
import time

from codev_platform.core.config import get, load_config
from codev_platform.reindex.pg_queue import PgJobQueue

DSN = os.environ.get("CODEV_PLATFORM_MEMORY_DSN") or get(load_config(), "memory.pg_dsn")
assert DSN, "no dsn"
TBL = f"reindex_jobs_haudit_{random.randint(100000, 999999)}"
assert TBL != "reindex_jobs"
print(f"[probe] table={TBL}")


def newq(owner=None):
    q = PgJobQueue(DSN, table=TBL)
    q._ensure()
    if owner is not None:
        q._owner = owner
    return q


def row(q, pid):
    with q._pool.connection() as c:
        return c.execute(
            f"SELECT status, claimed_by FROM {TBL} WHERE project_id=%s", (pid,)
        ).fetchone()


def clean(q):
    with q._pool.connection() as c:
        c.execute(f"DELETE FROM {TBL}")


def drop(q):
    with q._pool.connection() as c:
        c.execute(f"DROP TABLE IF EXISTS {TBL}")


qsetup = newq()
clean(qsetup)
results = {}

# ---- #1 崩溃恢复 owner 稳定性 (重点) ----
# qA 含 pid 'host:111' 认领 -> running. 新进程重启 = 新 pid 'host:222' (qB).
# qB.reclaim_stale_own() 能复位 qA 留下的 stale 行吗?
qA = newq("host:111")
qA.enqueue("p1-crash", "chroma")
claimed = qA.pending()
assert any(j.project_id == "p1-crash" for j in claimed), "qA failed to claim"
st = row(qA, "p1-crash")
print(f"[#1] after qA claim: {st}")  # ('running','host:111')

qB = newq("host:222")  # simulated restart, new pid -> new owner
n_reset = qB.reclaim_stale_own()
st_after = row(qB, "p1-crash")
print(f"[#1] qB(pid222) reclaim_stale_own()={n_reset}; row after={st_after}")
# Can qB now pick it up immediately (without waiting 1800s lease)?
picks = [j for j in qB.pending() if j.project_id == "p1-crash"]
crash_recovery_works = (n_reset >= 1 and st_after[0] == "pending" and len(picks) == 1)
results["#1 crash recovery on real restart (new pid)"] = (
    "WORKS" if crash_recovery_works else "BUG: reclaim no-op, row stuck running, new worker cannot recover without 1800s lease"
)

clean(qsetup)

# ---- #1-fix probe: would host-level owner fix it? ----
# Simulate: owner = host only. Restart keeps same host -> reclaim hits.
qFa = newq("HOSTONLY")
qFa.enqueue("p1-fix", "chroma")
qFa.pending()
qFb = newq("HOSTONLY")  # restart: pid changed but owner (host) stable
n_fix = qFb.reclaim_stale_own()
st_fix = row(qFb, "p1-fix")
picks_fix = [j for j in qFb.pending() if j.project_id == "p1-fix"]
host_fix_works = (n_fix >= 1 and st_fix[0] == "pending" and len(picks_fix) == 1)
# cross-host no misfire: another host's running row untouched
clean(qsetup)
qFa2 = newq("HOSTONLY")
qFa2.enqueue("p1-cross", "chroma")
with qFa2._pool.connection() as c:
    c.execute(
        f"UPDATE {TBL} SET status='running', claimed_by='OTHERHOST', "
        "lease_expires_at=%s, claim_token='t' WHERE project_id='p1-cross'",
        (time.time() + 1800,),
    )
qFb2 = newq("HOSTONLY")
qFb2.reclaim_stale_own()
st_cross = row(qFb2, "p1-cross")
results["#1-fix host-level owner"] = (
    f"host-level reclaim works={host_fix_works}; cross-host('OTHERHOST') untouched={st_cross[0]=='running'}"
)

clean(qsetup)

# ---- #2 亲和孤儿静默积压 ----
qO = newq()
qO.enqueue("orphan-org", "chroma")
claimed_mine = qO.pending({"mine-only"})  # whitelist excludes orphan
orphan_claimed = any(j.project_id == "orphan-org" for j in claimed_mine)
peek_visible = any(j.project_id == "orphan-org" for j in qO.peek())
results["#2 orphan: claimed by mismatched whitelist"] = (
    f"claimed={orphan_claimed} (expect False=stuck); peek_visible={peek_visible} (visible but no proactive alert)"
)

clean(qsetup)

# ---- #3 None claims all ----
qN = newq()
qN.enqueue("other-org-a", "chroma")
qN.enqueue("other-org-b", "codegraph")
claimed_all = {(j.project_id, j.kind) for j in qN.pending(None)}
none_eats_all = (
    ("other-org-a", "chroma") in claimed_all and ("other-org-b", "codegraph") in claimed_all
)
results["#3 None claims all (misconfig -> eats other-org)"] = (
    f"None claimed all={none_eats_all} (multi-machine misconfig = regress to eating others)"
)

clean(qsetup)

# ---- #5 affinity concurrency exactly-once (overlapping whitelist) ----
qS = newq()
qS.enqueue("shared", "chroma")
qx = newq()
qy = newq()
cx = [j for j in qx.pending({"shared"}) if j.project_id == "shared"]
cy = [j for j in qy.pending({"shared"}) if j.project_id == "shared"]
exactly_once = (len(cx) + len(cy) == 1)
results["#5 overlapping whitelist exactly-once"] = (
    f"x={len(cx)} y={len(cy)} exactly_once={exactly_once}"
)

clean(qsetup)
drop(qsetup)
print("\n========== RESULTS ==========")
for k, v in results.items():
    print(f"  {k}\n     -> {v}")
print("\n[probe] table dropped:", TBL)
