"""依赖基座 purelib 静态发行包清单测试。"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
from pathlib import Path

import pytest

from codev_platform import runtime_distribution_inventory as inventory
from codev_platform.runtime_dependency_contract import DistributionPin


def _record_content(files: dict[str, bytes], record_relative: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for relative, content in files.items():
        encoded = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        writer.writerow((relative, f"sha256={encoded.decode('ascii')}", len(content)))
    writer.writerow((record_relative, "", ""))
    return output.getvalue().encode("utf-8")


def _install_distribution(
    purelib: Path,
    name: str,
    version: str,
    *,
    extra: dict[str, bytes] | None = None,
    claimed: dict[str, bytes] | None = None,
) -> Path:
    package = name.replace("-", "_")
    dist_info = f"{package}-{version}.dist-info"
    metadata_relative = f"{dist_info}/METADATA"
    record_relative = f"{dist_info}/RECORD"
    files = {
        f"{package}/__init__.py": f"VERSION = {version!r}\n".encode(),
        metadata_relative: (f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode()),
        **(extra or {}),
    }
    record_files = dict(files)
    record_files.update(claimed or {})
    files[record_relative] = _record_content(record_files, record_relative)
    for relative, content in files.items():
        target = purelib / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return purelib / dist_info


def test_exact_distribution_inventory_returns_stable_digest(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "demo-package", "1.2.3")
    pins = (DistributionPin("demo-package", "1.2.3"),)

    first = inventory.verify_distribution_inventory(purelib, pins)
    second = inventory.verify_distribution_inventory(purelib, pins)

    assert first == second
    assert first.distributions == ("demo-package==1.2.3",)
    assert first.file_count == 3
    assert len(first.inventory_sha256) == 64


def test清单验证支持worker当前目录下的相对purelib(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "demo-package", "1.2.3")
    monkeypatch.chdir(tmp_path)

    proof = inventory.verify_distribution_inventory(
        Path("site-packages"),
        (DistributionPin("demo-package", "1.2.3"),),
    )

    assert proof.distributions == ("demo-package==1.2.3",)


@pytest.mark.parametrize("mutation", ["unknown", "changed", "missing", "empty_dir"])
def test_inventory_rejects_unproven_or_drifted_files(
    tmp_path: Path,
    mutation: str,
) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "demo", "1.0")
    package = purelib / "demo/__init__.py"
    if mutation == "unknown":
        (purelib / "unknown.py").write_bytes(b"unexpected")
    elif mutation == "changed":
        package.write_bytes(b"drifted")
    elif mutation == "missing":
        package.unlink()
    else:
        (purelib / "empty").mkdir()

    with pytest.raises(inventory.RuntimeDistributionInventoryError):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "1.0"),),
        )


def test_inventory_requires_exact_lock_name_and_version(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "demo", "1.0")

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="精确 pin"):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "2.0"),),
        )


def test_inventory_accepts_identical_shared_record_ownership(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "first", "1.0")
    first_relative = "first/__init__.py"
    _install_distribution(
        purelib,
        "second",
        "1.0",
        claimed={first_relative: (purelib / first_relative).read_bytes()},
    )

    proof = inventory.verify_distribution_inventory(
        purelib,
        (
            DistributionPin("first", "1.0"),
            DistributionPin("second", "1.0"),
        ),
    )

    assert proof.distributions == ("first==1.0", "second==1.0")
    assert proof.file_count == 6


def test_inventory_rejects_conflicting_shared_record_ownership(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "first", "1.0")
    _install_distribution(
        purelib,
        "second",
        "1.0",
        claimed={"first/__init__.py": b"different\n"},
    )

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="声明冲突"):
        inventory.verify_distribution_inventory(
            purelib,
            (
                DistributionPin("first", "1.0"),
                DistributionPin("second", "1.0"),
            ),
        )


def test_inventory_rejects_executable_pth_before_python(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(
        purelib,
        "demo",
        "1.0",
        extra={"demo-startup.pth": b"import os\n"},
    )

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="可执行启动代码"):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "1.0"),),
        )


def test_inventory_accepts_explicit_owner_and_digest_locked_pth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purelib = tmp_path / "site-packages"
    content = b"import approved_bootstrap\n"
    relative = "approved-bootstrap.pth"
    _install_distribution(
        purelib,
        "demo",
        "1.0",
        extra={relative: content},
    )
    monkeypatch.setattr(
        inventory,
        "_APPROVED_EXECUTABLE_PTH",
        {
            (relative, ("demo",)): hashlib.sha256(content).hexdigest(),
        },
    )

    proof = inventory.verify_distribution_inventory(
        purelib,
        (DistributionPin("demo", "1.0"),),
    )

    assert proof.file_count == 4


def test_inventory_rejects_pth_replaced_between_snapshot_and_policy_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purelib = tmp_path / "site-packages"
    approved = b"import approved_bootstrap\n"
    relative = "approved-bootstrap.pth"
    _install_distribution(
        purelib,
        "demo",
        "1.0",
        extra={relative: approved},
    )
    monkeypatch.setattr(
        inventory,
        "_APPROVED_EXECUTABLE_PTH",
        {
            (relative, ("demo",)): hashlib.sha256(approved).hexdigest(),
        },
    )
    original_snapshot = inventory._snapshot_file
    replaced = False

    def _racing_snapshot(path: Path, *, max_bytes: int, collect: bool):
        nonlocal replaced
        result = original_snapshot(path, max_bytes=max_bytes, collect=collect)
        if path.name == relative and not replaced:
            path.write_bytes(b"import malicious_bootstrap\n")
            replaced = True
        return result

    monkeypatch.setattr(inventory, "_snapshot_file", _racing_snapshot)

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="检查期间"):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "1.0"),),
        )


def test_inventory_rejects_pth_aggregate_memory_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purelib = tmp_path / "site-packages"
    first = b"# first payload\n"
    second = b"# second payload\n"
    _install_distribution(
        purelib,
        "demo",
        "1.0",
        extra={"first.pth": first, "second.pth": second},
    )
    monkeypatch.setattr(
        inventory,
        "_MAX_PTH_TOTAL_BYTES",
        len(first) + len(second) - 1,
    )

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="聚合.*安全预算"):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "1.0"),),
        )


@pytest.mark.parametrize("name", ["sitecustomize.py", "usercustomize.py", "foreign.egg-link"])
def test_inventory_rejects_startup_injection_even_when_recorded(
    tmp_path: Path,
    name: str,
) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "demo", "1.0", extra={name: b"malicious\n"})

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="启动注入"):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "1.0"),),
        )


def test_inventory_rejects_record_row_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "demo", "1.0")
    monkeypatch.setattr(inventory, "_MAX_RECORD_ROWS", 2)

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="行数.*安全预算"):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "1.0"),),
        )


def test_inventory_rejects_symlink_without_following(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows 普通用户不保证可创建符号链接")
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, "demo", "1.0")
    target = purelib / "demo/__init__.py"
    target.unlink()
    target.symlink_to(tmp_path / "outside.py")

    with pytest.raises(inventory.RuntimeDistributionInventoryError, match="链接"):
        inventory.verify_distribution_inventory(
            purelib,
            (DistributionPin("demo", "1.0"),),
        )
