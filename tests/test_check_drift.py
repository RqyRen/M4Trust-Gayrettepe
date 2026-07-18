"""`contracts/scripts/check_drift.py` testleri (Berke'nin "minimum kapı"
listesindeki madde 10: contract drift CI kontrolü).

Gercek `contracts/` dizinine dokunmaz -- `tmp_path` altinda sahte bir "bizim
repo" ve sahte bir "Spring repo" klasoru kurup `--root`/`--sync` ile izole
calisir.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "contracts" / "scripts" / "check_drift.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(_SCRIPT), *args], capture_output=True, text=True)


def _make_repo(root: Path, *, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


_BASE_FILES = {
    "schemas/common/error-1.0.0.schema.json": '{"type": "object"}',
    "asyncapi/m4trust-ai-v1.yaml": "asyncapi: 3.0.0\n",
    "examples/job/cancel-request.json": '{"reason": "USER_REQUESTED"}',
    "openapi/ai-internal-v1.yaml": "openapi: 3.1.0\n",
    "README.md": "# contracts\n",
    "CHANGELOG.md": "## Unreleased\n",
}


def test_check_fails_without_a_manifest(tmp_path: Path) -> None:
    _make_repo(tmp_path, files=_BASE_FILES)
    result = _run("--check", "--root", str(tmp_path))
    assert result.returncode == 2
    assert "no manifest found" in result.stderr


def test_sync_writes_manifest_when_repos_match(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    _make_repo(spring / "contracts", files=_BASE_FILES)
    subprocess.run(["git", "init", "--quiet"], cwd=spring, check=True)

    result = _run("--sync", str(spring), "--root", str(ours / "contracts"))

    assert result.returncode == 0, result.stderr
    manifest = json.loads((ours / "contracts" / ".sync-manifest.json").read_text(encoding="utf-8"))
    assert manifest["sourceRepo"] == "m4trust-spring-front-prod"
    assert set(manifest["files"]) == set(_BASE_FILES)


def test_check_passes_immediately_after_sync(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    _make_repo(spring / "contracts", files=_BASE_FILES)

    _run("--sync", str(spring), "--root", str(ours / "contracts"))
    result = _run("--check", "--root", str(ours / "contracts"))

    assert result.returncode == 0
    assert "PASS" in result.stdout


def test_check_catches_local_edit_after_sync(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    _make_repo(spring / "contracts", files=_BASE_FILES)
    _run("--sync", str(spring), "--root", str(ours / "contracts"))

    # Bulgu senaryosu: birisi Spring'den gecmeden bir schema'yi elle degistirdi.
    (ours / "contracts" / "schemas" / "common" / "error-1.0.0.schema.json").write_text(
        '{"type": "object", "tampered": true}', encoding="utf-8"
    )

    result = _run("--check", "--root", str(ours / "contracts"))

    assert result.returncode == 1
    assert "changed since last sync" in result.stderr
    assert "error-1.0.0.schema.json" in result.stderr


def test_sync_refuses_when_upstream_differs_without_force(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    spring_files = dict(_BASE_FILES)
    spring_files["schemas/common/error-1.0.0.schema.json"] = '{"type": "object", "changedUpstream": true}'
    _make_repo(spring / "contracts", files=spring_files)

    result = _run("--sync", str(spring), "--root", str(ours / "contracts"))

    assert result.returncode == 1
    assert "differs from Spring's contracts/" in result.stderr
    assert not (ours / "contracts" / ".sync-manifest.json").exists()


def test_sync_detects_upstream_only_files(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    spring_files = dict(_BASE_FILES)
    spring_files["schemas/document-extraction/new-thing-1.0.0.schema.json"] = '{"type": "object"}'
    _make_repo(spring / "contracts", files=spring_files)

    result = _run("--sync", str(spring), "--root", str(ours / "contracts"))

    assert result.returncode == 1
    assert "only in Spring's contracts/ (not copied here yet): schemas/document-extraction/new-thing-1.0.0.schema.json" in result.stderr


def test_sync_excludes_known_spring_only_core_api_file(tmp_path: Path) -> None:
    # openapi/core-api-v1.yaml Spring'in kendi public API'si -- AI worker'i
    # ilgilendirmiyor, kasitli olarak burada kopyalanmiyor (bkz. script'in
    # EXCLUDE_FROM_UPSTREAM_CHECK'i).
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    spring_files = dict(_BASE_FILES)
    spring_files["openapi/core-api-v1.yaml"] = "openapi: 3.1.0\n"
    _make_repo(spring / "contracts", files=spring_files)

    result = _run("--sync", str(spring), "--root", str(ours / "contracts"))

    assert result.returncode == 0, result.stderr


def test_sync_with_force_writes_manifest_despite_differences(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    spring_files = dict(_BASE_FILES)
    spring_files["schemas/common/error-1.0.0.schema.json"] = '{"type": "object", "changedUpstream": true}'
    _make_repo(spring / "contracts", files=spring_files)

    result = _run("--sync", str(spring), "--root", str(ours / "contracts"), "--force")

    assert result.returncode == 0, result.stderr
    assert (ours / "contracts" / ".sync-manifest.json").exists()
    assert "WARNING --force used with unresolved differences" in result.stderr


def test_sync_records_spring_repo_commit_sha(tmp_path: Path) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    _make_repo(spring / "contracts", files=_BASE_FILES)
    subprocess.run(["git", "init", "--quiet"], cwd=spring, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=spring, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=spring, check=True)
    subprocess.run(["git", "add", "-A"], cwd=spring, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "initial"], cwd=spring, check=True)
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=spring, capture_output=True, text=True, check=True
    ).stdout.strip()

    _run("--sync", str(spring), "--root", str(ours / "contracts"))

    manifest = json.loads((ours / "contracts" / ".sync-manifest.json").read_text(encoding="utf-8"))
    assert manifest["sourceCommit"] == expected_sha


@pytest.mark.parametrize("missing_file", list(_BASE_FILES))
def test_check_catches_a_deleted_tracked_file(tmp_path: Path, missing_file: str) -> None:
    ours = tmp_path / "ours"
    spring = tmp_path / "spring"
    _make_repo(ours / "contracts", files=_BASE_FILES)
    _make_repo(spring / "contracts", files=_BASE_FILES)
    _run("--sync", str(spring), "--root", str(ours / "contracts"))

    (ours / "contracts" / missing_file).unlink()

    result = _run("--check", "--root", str(ours / "contracts"))

    assert result.returncode == 1
    assert f"missing (recorded in manifest, not on disk): {missing_file}" in result.stderr
