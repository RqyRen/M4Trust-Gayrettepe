#!/usr/bin/env python3
"""Contract sync-drift manifest tool.

`contracts/` in this repo is a manually synced copy of the authoritative
`contracts/` directory in Berke's `m4trust-spring-front-prod` repo (Spring is
contract authority -- see contracts/README.md). There is no git submodule
link between the two repos (polyrepo governance is still an open ADR on
Berke's side), so this repo cannot automatically detect when Spring's copy
changes on its own -- something has to actually fetch and diff it.

`m4trust-spring-front-prod` is a PUBLIC repo (verified 20 Jul 2026 via the
GitHub API: `"private": false`), so that fetch needs no credential -- a plain
`git clone`/`actions/checkout` works. An earlier note in this project assumed
it was private and deferred automation for that reason; that assumption was
wrong. The `contract-drift` job in `.github/workflows/ci.yml` runs this
script's --sync mode on a weekly schedule (plus on-demand via
workflow_dispatch) against a fresh checkout of Spring's repo.

Two modes:

  --check (default, CI-safe, runs on every push/PR): recomputes SHA-256 for
      every tracked contract file and compares against
      `contracts/.sync-manifest.json`, written by the last `--sync`. This
      does NOT reach across to Spring's repo -- it only catches contract
      files changing since the last recorded sync (e.g. someone editing a
      schema by hand without going through Spring). It cannot, by itself,
      detect that Spring changed and we haven't synced.

  --sync SPRING_REPO_PATH (both repos checked out on disk -- locally, or in
      the scheduled CI job): byte-for-byte diffs every tracked file here
      against the same path in SPRING_REPO_PATH/contracts/, refuses to write
      a manifest if anything differs (use --force to override after
      reviewing), then records SHA-256 hashes plus Spring's current commit
      SHA into the manifest. This is the actual "did we drift" check;
      --check just keeps that manifest honest between syncs. The CI job
      intentionally does not commit the manifest on your behalf -- a real
      diff found there means a human should review it and push a --sync PR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # contracts/

# Spring owns these; they should never be hand-edited here without going
# through a resync. `scripts/` and `requirements.txt` are excluded on
# purpose -- local validation tooling, allowed to differ (contracts/README.md).
TRACKED_SUBDIRS = ("schemas", "asyncapi", "examples", "openapi")
TRACKED_FILES = ("README.md", "CHANGELOG.md")

# Files that exist upstream but are deliberately NOT mirrored here because
# they're outside the AI worker's concern (Spring's public-facing Core API,
# not part of the Spring<->AI contract).
EXCLUDE_FROM_UPSTREAM_CHECK = {"openapi/core-api-v1.yaml"}


def _tracked_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for subdir in TRACKED_SUBDIRS:
        dir_path = base / subdir
        if dir_path.is_dir():
            files.extend(p for p in dir_path.rglob("*") if p.is_file())
    for name in TRACKED_FILES:
        path = base / name
        if path.is_file():
            files.append(path)
    return sorted(files)


def _sha256(path: Path) -> str:
    # Normalize CRLF -> LF before hashing: these are all text files (JSON/
    # YAML/MD), and git's autocrlf checks them out as CRLF on Windows but LF
    # on CI's Linux runner. Without this, every tracked file "changes" on
    # every platform switch even though the content is identical.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _rel(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def cmd_check(root: Path = ROOT) -> int:
    manifest_path = root / ".sync-manifest.json"
    if not manifest_path.exists():
        print(f"FAIL no manifest found at {manifest_path} -- run --sync first", file=sys.stderr)
        return 2

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded: dict[str, str] = manifest.get("files", {})
    current = {_rel(p, root): _sha256(p) for p in _tracked_files(root)}

    missing = sorted(set(recorded) - set(current))  # in manifest, gone from disk
    added = sorted(set(current) - set(recorded))  # on disk, never synced
    changed = sorted(k for k in (set(recorded) & set(current)) if recorded[k] != current[k])

    if not missing and not added and not changed:
        source_commit = (manifest.get("sourceCommit") or "unknown")[:12]
        print(
            f"PASS contracts/ matches sync manifest ({len(current)} files, synced from "
            f"{manifest.get('sourceRepo')}@{source_commit} on {manifest.get('syncedAt')})"
        )
        return 0

    for path in missing:
        print(f"FAIL missing (recorded in manifest, not on disk): {path}", file=sys.stderr)
    for path in added:
        print(f"FAIL untracked (on disk, never synced): {path}", file=sys.stderr)
    for path in changed:
        print(f"FAIL changed since last sync: {path}", file=sys.stderr)
    print(
        "\nContract files differ from the last recorded sync with the authoritative "
        "m4trust-spring-front-prod repo. If this is an intentional resync, re-run "
        "`--sync <path-to-spring-repo>` locally. If it isn't, someone edited a contract "
        "file without going through Spring -- Spring owns contract authority "
        "(contracts/README.md).",
        file=sys.stderr,
    )
    return 1


def cmd_sync(spring_repo: Path, *, force: bool, root: Path = ROOT) -> int:
    spring_contracts = spring_repo / "contracts"
    if not spring_contracts.is_dir():
        print(f"FAIL {spring_contracts} does not look like a checkout of m4trust-spring-front-prod", file=sys.stderr)
        return 2

    mismatches: list[str] = []
    for path in _tracked_files(root):
        rel = _rel(path, root)
        upstream = spring_contracts / rel
        if not upstream.is_file():
            mismatches.append(f"only in this repo (not in Spring's contracts/): {rel}")
        elif upstream.read_bytes().replace(b"\r\n", b"\n") != path.read_bytes().replace(b"\r\n", b"\n"):
            mismatches.append(f"differs from Spring's contracts/: {rel}")

    upstream_files = {_rel(p, spring_contracts) for p in _tracked_files(spring_contracts)}
    our_files = {_rel(p, root) for p in _tracked_files(root)}
    only_upstream = upstream_files - our_files - EXCLUDE_FROM_UPSTREAM_CHECK
    for rel in sorted(only_upstream):
        mismatches.append(f"only in Spring's contracts/ (not copied here yet): {rel}")

    if mismatches and not force:
        print("FAIL contracts/ has drifted from the authoritative Spring repo:", file=sys.stderr)
        for line in sorted(mismatches):
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nThis script does not copy files for you -- Spring owns contract authority "
            "(contracts/README.md). Review each difference, update contracts/ by hand, then "
            "re-run --sync. Use --force to record the manifest anyway (only if you've "
            "deliberately reviewed and accepted every difference above).",
            file=sys.stderr,
        )
        return 1

    try:
        source_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=spring_repo, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        source_commit = None

    manifest = {
        "syncedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sourceRepo": "m4trust-spring-front-prod",
        "sourceCommit": source_commit,
        "files": {_rel(p, root): _sha256(p) for p in _tracked_files(root)},
    }
    manifest_path = root / ".sync-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"OK wrote {manifest_path} ({len(manifest['files'])} files, source commit "
        f"{source_commit[:12] if source_commit else 'unknown'})"
    )
    if mismatches:
        print("WARNING --force used with unresolved differences:", file=sys.stderr)
        for line in sorted(mismatches):
            print(f"  - {line}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="verify contracts/ matches the recorded sync manifest (default, CI-safe)"
    )
    mode.add_argument(
        "--sync",
        metavar="SPRING_REPO_PATH",
        help="diff against a local m4trust-spring-front-prod checkout and rewrite the manifest",
    )
    parser.add_argument("--force", action="store_true", help="with --sync, write the manifest even if differences were found")
    parser.add_argument("--root", metavar="PATH", help=argparse.SUPPRESS)  # test hook; defaults to contracts/
    args = parser.parse_args()

    root = Path(args.root) if args.root else ROOT
    if args.sync:
        return cmd_sync(Path(args.sync), force=args.force, root=root)
    return cmd_check(root)


if __name__ == "__main__":
    raise SystemExit(main())
