"""特权 systemd 固定 unit 契约测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallTransactionError,
    SystemdRuntimeBinding,
    SystemdUnitActivationMode,
    SystemdUnitInstallSpec,
    SystemdUnitPayload,
)
from codev_platform.mcp_systemd_managed_contract import verify_managed_install_contract
from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNITS


def _managed_input(tmp_path: Path) -> tuple[SystemdInstallManifest, tuple[SystemdUnitPayload, ...]]:
    specs: list[SystemdUnitInstallSpec] = []
    payloads: list[SystemdUnitPayload] = []
    for registration in MANAGED_SYSTEMD_UNITS.values():
        content = f"[Unit]\nDescription={registration.name}\n".encode()
        spec = SystemdUnitInstallSpec(
            source=tmp_path / registration.name,
            content_digest=hashlib.sha256(content).hexdigest(),
            enable=registration.enable,
            restart=registration.restart,
            activation_mode=SystemdUnitActivationMode(registration.activation_mode),
        )
        specs.append(spec)
        payloads.append(SystemdUnitPayload(spec, content))
    binding = SystemdRuntimeBinding(
        release_root="/var/lib/codev-platform/runtime",
        expected_release_id="2" * 64,
        target_user="service-user",
    )
    return SystemdInstallManifest(tuple(specs), "1" * 40, binding), tuple(payloads)


def test_exact_managed_registry_is_accepted(tmp_path: Path) -> None:
    manifest, payloads = _managed_input(tmp_path)

    verify_managed_install_contract(manifest, payloads)


@pytest.mark.parametrize("mutation", ["missing", "extra", "lifecycle"])
def test_registry_drift_is_rejected_before_install(tmp_path: Path, mutation: str) -> None:
    manifest, payloads = _managed_input(tmp_path)
    specs = list(manifest.units)
    values = list(payloads)
    if mutation == "missing":
        specs.pop()
        values.pop()
    elif mutation == "extra":
        content = b"[Unit]\nDescription=extra\n"
        spec = SystemdUnitInstallSpec(
            source=tmp_path / "codev-extra.service",
            content_digest=hashlib.sha256(content).hexdigest(),
            enable=False,
            restart=False,
        )
        specs.append(spec)
        values.append(SystemdUnitPayload(spec, content))
    else:
        original = specs[0]
        replacement = SystemdUnitInstallSpec(
            source=original.source,
            content_digest=original.content_digest,
            enable=not original.enable,
            restart=original.restart,
            activation_mode=original.activation_mode,
        )
        specs[0] = replacement
        values[0] = SystemdUnitPayload(replacement, values[0].content)

    with pytest.raises(SystemdInstallTransactionError, match="受管.*unit|固定受管"):
        verify_managed_install_contract(
            SystemdInstallManifest(
                tuple(specs),
                manifest.runtime_revision,
                manifest.runtime_binding,
            ),
            tuple(values),
        )


def test_legacy_manifest_without_release_binding_is_rejected(tmp_path: Path) -> None:
    manifest, payloads = _managed_input(tmp_path)
    legacy = SystemdInstallManifest(manifest.units, manifest.runtime_revision)

    with pytest.raises(SystemdInstallTransactionError, match="缺少 release"):
        verify_managed_install_contract(legacy, payloads)
