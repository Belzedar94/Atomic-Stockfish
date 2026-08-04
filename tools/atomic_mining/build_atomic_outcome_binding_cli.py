"""Operational CLI for a clean, create-new Atomic rules binding build.

The compiler-facing implementation lives in
``build_atomic_outcome_binding`` because the E00 runner authenticates that
module as part of its runtime TCB.  This thin, separate module adds the
operational gates needed before a production build without changing the
imported runner wire:

* the requested source is the exact Git worktree root;
* HEAD commit/tree are explicitly precommitted and the full worktree is clean;
* the output lives outside that worktree and is create-new;
* the same commit/tree/clean state still holds after the native build; and
* the published binding and build manifest are reopened byte-for-byte before
  the CLI reports success.

The native build manifest remains the binding contract consumed by E00.  A
non-zero CLI exit means the output directory is terminal and must never be
reused, even if the lower-level builder left diagnostic artifacts there.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

from . import build_atomic_outcome_binding, common


_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")


class NativeBindingCliError(RuntimeError):
    """The operational native-binding build contract was not satisfied."""


@dataclass(frozen=True)
class SourceIdentity:
    root: Path
    commit: str
    tree: str
    clean: bool


def _git(
    source_root: Path,
    *arguments: str,
) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30.0,
        )
    except (
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        UnicodeError,
    ) as error:
        raise NativeBindingCliError(
            "cannot authenticate native-binding source with Git"
        ) from error
    return completed.stdout.strip()


def _source_identity(source_root: Path) -> SourceIdentity:
    requested = Path(source_root).expanduser().resolve()
    if not requested.is_dir():
        raise NativeBindingCliError("source root is not a directory")
    root = Path(_git(requested, "rev-parse", "--show-toplevel")).resolve()
    if root != requested:
        raise NativeBindingCliError(
            "source root must be the exact Git worktree root"
        )
    commit = _git(root, "rev-parse", "HEAD").lower()
    tree = _git(root, "rev-parse", "HEAD^{tree}").lower()
    if _GIT_OBJECT.fullmatch(commit) is None or _GIT_OBJECT.fullmatch(tree) is None:
        raise NativeBindingCliError("Git returned a malformed commit or tree")
    dirty = _git(
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    return SourceIdentity(root=root, commit=commit, tree=tree, clean=dirty == "")


def _outside_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return True
    return False


def _expected_git_object(value: str, *, label: str) -> str:
    rendered = str(value).lower()
    if _GIT_OBJECT.fullmatch(rendered) is None:
        raise NativeBindingCliError(f"{label} must be 40 lowercase hex characters")
    return rendered


def build_from_clean_source(
    source_root: Path,
    output_dir: Path,
    *,
    expected_source_commit: str,
    expected_source_tree: str,
    timeout_seconds: float = 300.0,
) -> dict[str, object]:
    """Build and reopen one native binding from an exact clean worktree."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise NativeBindingCliError("timeout_seconds must be finite and positive")
    expected_commit = _expected_git_object(
        expected_source_commit, label="expected source commit"
    )
    expected_tree = _expected_git_object(
        expected_source_tree, label="expected source tree"
    )
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"refusing to reuse binding output: {output}")

    before = _source_identity(source_root)
    if (
        before.commit != expected_commit
        or before.tree != expected_tree
        or not before.clean
    ):
        raise NativeBindingCliError(
            "source commit/tree/clean state differs before native build"
        )
    if not _outside_root(output, before.root):
        raise NativeBindingCliError(
            "native build output must be outside the authenticated source root"
        )

    result = build_atomic_outcome_binding.build_native_rules_binding(
        before.root,
        output,
        expected_source_commit=expected_commit,
        timeout_seconds=float(timeout_seconds),
    )

    after = _source_identity(before.root)
    if after != before:
        raise NativeBindingCliError(
            "source commit/tree/clean state changed during native build"
        )
    binding_payload = common.read_stable_file_bytes(
        result.binding_path, label="published native binding"
    )
    manifest_payload = common.read_stable_file_bytes(
        result.manifest_path, label="published native build manifest"
    )
    if (
        common.sha256_bytes(binding_payload) != result.binding_sha256
        or common.sha256_bytes(manifest_payload) != result.manifest_sha256
        or result.source_commit != expected_commit
    ):
        raise NativeBindingCliError(
            "published native binding bundle differs after build"
        )
    return {
        "binding": {
            "path": str(result.binding_path.resolve()),
            "sha256": result.binding_sha256,
            "size_bytes": len(binding_payload),
        },
        "build_manifest": {
            "path": str(result.manifest_path.resolve()),
            "sha256": result.manifest_sha256,
            "size_bytes": len(manifest_payload),
        },
        "schema": "atomic-e00-native-outcome-build-cli-summary-v1",
        "source": {
            "commit": expected_commit,
            "root": str(before.root),
            "tree": expected_tree,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a create-new native Atomic rules binding from an exact "
            "clean Git commit/tree."
        )
    )
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-source-tree", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = build_from_clean_source(
            arguments.source_root,
            arguments.output_dir,
            expected_source_commit=arguments.expected_source_commit,
            expected_source_tree=arguments.expected_source_tree,
            timeout_seconds=arguments.timeout_seconds,
        )
    except (
        NativeBindingCliError,
        build_atomic_outcome_binding.AtomicOutcomeBuildError,
        common.MiningArtifactError,
        FileExistsError,
        OSError,
        ValueError,
    ) as error:
        print(f"native binding NO-GO: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(common.canonical_json_bytes(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "NativeBindingCliError",
    "SourceIdentity",
    "build_from_clean_source",
    "main",
]
