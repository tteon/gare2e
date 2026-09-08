from __future__ import annotations

import importlib.util
from pathlib import Path
import tarfile

import pytest


def _bundle_module():
    path = Path(__file__).parents[2] / "scripts" / "benchmarks" / "prepare_agentic_os_appliance_bundle.py"
    spec = importlib.util.spec_from_file_location("prepare_agentic_os_appliance_bundle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_bundle_excludes_local_state_and_is_checksum_addressed(tmp_path) -> None:
    module = _bundle_module()
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".seocho").mkdir()
    (repo / ".seocho" / "secret.txt").write_text("do-not-package", encoding="utf-8")
    (repo / "README.md").write_text("readme\n", encoding="utf-8")
    output = tmp_path / "bundle.tar.gz"

    receipt = module.create_bundle(repo_root=repo, output=output, include=("src", "README.md", ".seocho"))

    assert receipt["bytes"] == output.stat().st_size
    with tarfile.open(output, "r:gz") as archive:
        names = archive.getnames()
    assert "src/main.py" in names
    assert not any(".seocho" in name for name in names)


def test_source_bundle_normalizes_shell_entrypoints_to_executable(tmp_path) -> None:
    module = _bundle_module()
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    entrypoint = scripts / "entrypoint.sh"
    entrypoint.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
    entrypoint.chmod(0o644)
    output = tmp_path / "bundle.tar.gz"

    module.create_bundle(repo_root=repo, output=output, include=("scripts",))

    with tarfile.open(output, "r:gz") as archive:
        member = archive.getmember("scripts/entrypoint.sh")
    assert member.mode & 0o111


def test_source_bundle_refuses_unsafe_or_existing_output(tmp_path) -> None:
    module = _bundle_module()
    with pytest.raises(ValueError, match="safe repo-relative"):
        module._safe_relative("../outside")
    output = tmp_path / "exists.tar.gz"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        module.create_bundle(repo_root=tmp_path, output=output, include=())


def test_default_bundle_ships_runtime_package_for_gtsqa_lane_runners() -> None:
    module = _bundle_module()
    assert "runtime" in module.DEFAULT_INCLUDE
    assert "src" in module.DEFAULT_INCLUDE
