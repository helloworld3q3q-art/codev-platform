#!/usr/bin/env bash
# verify_clean_install.sh
#
# End-to-end proof for audit #2 (rules/skills relocate into codev_platform/resources/):
# a plain, NON-editable pip install of the built wheel must still be able to run
# `sync-rules` / `sync-skills`. That only works if the resources travel inside the
# wheel as package-data and importlib.resources can locate them from site-packages.
#
# Steps:
#   1. Build a wheel of THIS package only (--no-deps, no heavy ML deps).
#   2. Create a fresh venv and `pip install` the wheel with --no-deps
#      (we are validating resource packaging + the console-script entry point,
#       not chromadb/etc).
#   3. From an EMPTY non-repo dir, run sync-rules/sync-skills --dry-run and assert
#      rc==0 AND output contains "files synced" (proves resources shipped in the wheel).
#   4. Clean up the temp dirs.
#
# This is a standalone shell verifier, NOT part of the pytest suite.
set -euo pipefail

# Resolve repo root from this script's location (scripts/..).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLATFORM_PY="${REPO_ROOT}/.venv/bin/python"

TMP_ROOT="$(mktemp -d /tmp/codev_clean_install.XXXXXX)"
WHEEL_DIR="${TMP_ROOT}/wheel"
VENV_DIR="${TMP_ROOT}/venv"
EMPTY_DIR="${TMP_ROOT}/empty_target"
mkdir -p "${WHEEL_DIR}" "${EMPTY_DIR}"

cleanup() { rm -rf "${TMP_ROOT}"; }
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

[ -x "${PLATFORM_PY}" ] || fail "platform venv python not found: ${PLATFORM_PY}"

# 1. Build wheel (this package only).
echo "[1/3] building wheel from ${REPO_ROOT} ..."
"${PLATFORM_PY}" -m pip wheel "${REPO_ROOT}" --no-deps -w "${WHEEL_DIR}" >/dev/null
WHEEL="$(ls "${WHEEL_DIR}"/codev_platform*.whl 2>/dev/null | head -1)"
[ -n "${WHEEL}" ] || fail "no codev_platform*.whl produced in ${WHEEL_DIR}"
echo "      built: $(basename "${WHEEL}")"

# 2. Fresh venv + non-editable install (--no-deps).
echo "[2/3] creating clean venv and installing wheel (--no-deps) ..."
"${PLATFORM_PY}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install "${WHEEL}" --no-deps >/dev/null
CLI="${VENV_DIR}/bin/codev-platform"
[ -x "${CLI}" ] || fail "console script not installed: ${CLI}"

# 3. From an empty non-repo dir, dry-run sync and assert resources shipped.
echo "[3/3] running sync-rules/sync-skills --dry-run from empty dir ..."
for kind in rules skills; do
    out="$(cd "${EMPTY_DIR}" && "${CLI}" "sync-${kind}" --dry-run 2>&1)" \
        || fail "sync-${kind} exited non-zero:\n${out}"
    echo "${out}" | grep -q "files synced" \
        || fail "sync-${kind} output missing 'files synced' (resources not in wheel?):\n${out}"
    echo "      sync-${kind}: OK"
done

echo "PASS: non-editable wheel install can sync rules/skills (audit #2 deliverable verified)."
