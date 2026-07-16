#!/usr/bin/env python3
"""Fail-closed trust gate for an Atomic-Stockfish release tag on ``main``.

The gate consumes either raw GitHub REST API responses saved in a directory or
queries those same endpoints with ``gh api``.  It deliberately authenticates
only structural Git/GitHub facts; review text remains a procedural/ruleset
gate and is never scraped here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


EXPECTED_REPOSITORY = "Belzedar94/Atomic-Stockfish"
EXPECTED_DEFAULT_BRANCH = "main"
API_VERSION = "2022-11-28"
MAX_API_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_GITHUB_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_EXECUTABLE_CHARS = 1024
MAX_PR_NUMBER = 2_147_483_647
COMMAND_TIMEOUT_SECONDS = 60
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
SOURCE_MODES = frozenset(("online", "saved"))


class MainTrustError(RuntimeError):
    """The repository, main ref, tag, checkout, or release PR is not trusted."""


def _reject_duplicate_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MainTrustError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _reject_non_json_constant(value: str) -> Any:
    raise MainTrustError("non-JSON numeric constant: " + value)


def _parse_json(payload: bytes, label: str) -> Dict[str, Any]:
    if len(payload) > MAX_API_RESPONSE_BYTES:
        raise MainTrustError(label + " exceeds the API response size limit")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except MainTrustError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise MainTrustError("invalid JSON in %s: %s" % (label, error)) from error
    if type(value) is not dict:
        raise MainTrustError(label + " must be one JSON object")
    return value


def _load_json(path: Path, label: str) -> Dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise MainTrustError(label + " must be a regular, non-symlink file: " + str(path))
    try:
        if path.stat().st_size > MAX_API_RESPONSE_BYTES:
            raise MainTrustError(label + " exceeds the API response size limit")
        payload = path.read_bytes()
    except OSError as error:
        raise MainTrustError("cannot read %s: %s" % (label, error)) from error
    return _parse_json(payload, label)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise MainTrustError(label + " must be a JSON object")
    return value


def _string(value: Any, label: str) -> str:
    if type(value) is not str or not value:
        raise MainTrustError(label + " must be a non-empty JSON string")
    return value


def _sha(value: Any, label: str) -> str:
    candidate = _string(value, label)
    if not SHA_RE.fullmatch(candidate):
        raise MainTrustError(label + " must be one lowercase 40-hex commit/object SHA")
    return candidate


def _exact(value: Any, expected: str, label: str) -> str:
    actual = _string(value, label)
    if actual != expected:
        raise MainTrustError("%s mismatch: %r != %r" % (label, actual, expected))
    return actual


def _positive_integer(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0 or value > MAX_PR_NUMBER:
        raise MainTrustError(
            "%s must be a positive JSON integer no greater than %d"
            % (label, MAX_PR_NUMBER)
        )
    return value


def _source_mode(value: Any) -> str:
    mode = _string(value, "source mode")
    if mode not in SOURCE_MODES:
        raise MainTrustError("source mode must be exactly 'online' or 'saved'")
    return mode


def _safe_executable(value: str, label: str) -> str:
    if not value or len(value) > MAX_EXECUTABLE_CHARS or any(
        character in value for character in "\x00\r\n"
    ):
        raise MainTrustError(
            "%s must be a non-empty single-line value of at most %d characters"
            % (label, MAX_EXECUTABLE_CHARS)
        )
    return value


def _decode_command_output(payload: bytes, label: str) -> str:
    if len(payload) > MAX_API_RESPONSE_BYTES:
        raise MainTrustError(label + " output exceeds the size limit")
    try:
        value = payload.decode("utf-8").strip()
    except UnicodeError as error:
        raise MainTrustError(label + " output is not valid UTF-8") from error
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise MainTrustError(label + " must emit exactly one non-empty line")
    return value


def _git_stdout(
    *, git_executable: str, repository_root: Path, arguments: Sequence[str], label: str
) -> str:
    executable = _safe_executable(git_executable, "git executable")
    command = [executable, "-C", str(repository_root), *arguments]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MainTrustError("git failed while reading %s: %s" % (label, error)) from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 500:
            detail = detail[:500] + "..."
        raise MainTrustError(
            "git failed while reading %s with exit %d%s"
            % (label, result.returncode, ": " + detail if detail else "")
        )
    return _decode_command_output(result.stdout, "git " + label)


def authenticate_checkout(
    *,
    repository_root: Path,
    git_executable: str,
    expected_checkout_sha: Optional[str] = None,
) -> str:
    """Authenticate the checkout from Git itself, never from workflow input alone.

    ``repository_root`` must be the exact worktree root reported by Git.  HEAD is
    then peeled to a commit with ``git rev-parse --verify HEAD^{commit}``.  An
    optional caller-provided SHA is only an additional equality assertion.
    """

    if repository_root.is_symlink() or not repository_root.is_dir():
        raise MainTrustError(
            "repository root must be a regular, non-symlink directory: "
            + str(repository_root)
        )
    try:
        requested_root = repository_root.resolve(strict=True)
    except OSError as error:
        raise MainTrustError("cannot resolve repository root: " + str(error)) from error
    reported_text = _git_stdout(
        git_executable=git_executable,
        repository_root=requested_root,
        arguments=("rev-parse", "--show-toplevel"),
        label="repository root",
    )
    try:
        reported_root = Path(reported_text).resolve(strict=True)
    except OSError as error:
        raise MainTrustError("cannot resolve Git-reported repository root") from error
    if os.path.normcase(str(reported_root)) != os.path.normcase(str(requested_root)):
        raise MainTrustError(
            "repository root does not exactly match git rev-parse --show-toplevel"
        )
    checkout = _sha(
        _git_stdout(
            git_executable=git_executable,
            repository_root=requested_root,
            arguments=("rev-parse", "--verify", "HEAD^{commit}"),
            label="checkout HEAD commit",
        ),
        "Git-authenticated checkout SHA",
    )
    if expected_checkout_sha is not None:
        expected = _sha(expected_checkout_sha, "expected checkout SHA")
        if checkout != expected:
            raise MainTrustError(
                "Git-authenticated checkout SHA does not match expected checkout SHA"
            )
    return checkout


def _validate_reference_snapshot(
    *,
    main_ref: Mapping[str, Any],
    tag_ref: Mapping[str, Any],
    tag_object: Mapping[str, Any],
    tag: str,
    checkout_sha: str,
    label: str,
) -> Dict[str, str]:
    main = _mapping(main_ref, label + " main ref response")
    _exact(main.get("ref"), "refs/heads/main", label + " main ref")
    main_target = _mapping(main.get("object"), label + " main ref object")
    _exact(main_target.get("type"), "commit", label + " main ref object type")
    main_sha = _sha(main_target.get("sha"), label + " main ref commit SHA")

    tag_response = _mapping(tag_ref, label + " tag ref response")
    _exact(tag_response.get("ref"), "refs/tags/" + tag, label + " tag ref")
    tag_target = _mapping(tag_response.get("object"), label + " tag ref object")
    _exact(tag_target.get("type"), "tag", label + " tag ref object type")
    tag_object_sha = _sha(
        tag_target.get("sha"), label + " annotated tag object SHA"
    )

    annotated = _mapping(tag_object, label + " annotated tag response")
    if _sha(annotated.get("sha"), label + " tag response SHA") != tag_object_sha:
        raise MainTrustError(
            label + " tag response SHA does not match the exact tag ref object"
        )
    _exact(annotated.get("tag"), tag, label + " annotated tag name")
    _mapping(annotated.get("tagger"), label + " annotated tag tagger")
    peeled = _mapping(
        annotated.get("object"), label + " annotated tag peeled object"
    )
    _exact(
        peeled.get("type"),
        "commit",
        label + " annotated tag peeled object type",
    )
    peeled_sha = _sha(
        peeled.get("sha"), label + " annotated tag peeled commit SHA"
    )

    if main_sha != checkout_sha:
        raise MainTrustError(
            label + " main ref commit SHA does not match the checkout SHA"
        )
    if peeled_sha != checkout_sha:
        raise MainTrustError(
            label + " tag peeled commit SHA does not match the checkout SHA"
        )
    return {
        "mainCommitSha": main_sha,
        "tagObjectSha": tag_object_sha,
        "tagCommitSha": peeled_sha,
    }


def validate_release_trust(
    *,
    repository: Mapping[str, Any],
    main_ref: Mapping[str, Any],
    tag_ref: Mapping[str, Any],
    tag_object: Mapping[str, Any],
    final_main_ref: Mapping[str, Any],
    final_tag_ref: Mapping[str, Any],
    final_tag_object: Mapping[str, Any],
    expected_repository: str,
    tag: str,
    checkout_sha: str,
    source_mode: str,
    release_pr_number: Optional[int] = None,
    release_pr: Optional[Mapping[str, Any]] = None,
    merge_commit: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate API documents and return their authenticated summary.

    Both the initial and final main/tag snapshots must independently resolve to
    the Git-authenticated checkout.  When a release PR is supplied, its merge
    commit is deliberately restricted to GitHub's traditional merge method:
    exactly two ordered parents, first the PR base and then the PR head.  Squash
    and rebase merges do not satisfy this release provenance contract.
    """

    if expected_repository != EXPECTED_REPOSITORY:
        raise MainTrustError(
            "repository argument must be exactly " + EXPECTED_REPOSITORY
        )
    if not TAG_RE.fullmatch(tag):
        raise MainTrustError("tag must be canonical vMAJOR.MINOR.PATCH")
    checkout = _sha(checkout_sha, "checkout SHA")
    mode = _source_mode(source_mode)

    repo = _mapping(repository, "repository response")
    _exact(repo.get("full_name"), EXPECTED_REPOSITORY, "repository full_name")
    _exact(
        repo.get("default_branch"),
        EXPECTED_DEFAULT_BRANCH,
        "repository default_branch",
    )

    initial_refs = _validate_reference_snapshot(
        main_ref=main_ref,
        tag_ref=tag_ref,
        tag_object=tag_object,
        tag=tag,
        checkout_sha=checkout,
        label="initial",
    )
    final_refs = _validate_reference_snapshot(
        main_ref=final_main_ref,
        tag_ref=final_tag_ref,
        tag_object=final_tag_object,
        tag=tag,
        checkout_sha=checkout,
        label="final",
    )
    if final_refs != initial_refs:
        raise MainTrustError("main or tag reference changed during trust validation")
    main_sha = initial_refs["mainCommitSha"]
    tag_object_sha = initial_refs["tagObjectSha"]
    peeled_sha = initial_refs["tagCommitSha"]

    pr_summary: Optional[Dict[str, Any]] = None
    optional_values = (release_pr_number, release_pr, merge_commit)
    if any(value is not None for value in optional_values):
        if any(value is None for value in optional_values):
            raise MainTrustError(
                "release PR number, PR response, and merge commit response "
                "must be supplied together"
            )
        expected_pr_number = _positive_integer(
            release_pr_number, "release PR number argument"
        )
        pr = _mapping(release_pr, "release PR response")
        if _positive_integer(pr.get("number"), "release PR number") != expected_pr_number:
            raise MainTrustError("release PR number mismatch")
        _exact(pr.get("state"), "closed", "release PR state")
        if pr.get("merged") is not True:
            raise MainTrustError("release PR is not merged")
        _string(pr.get("merged_at"), "release PR merged_at")

        base = _mapping(pr.get("base"), "release PR base")
        _exact(base.get("ref"), "main", "release PR base ref")
        base_repo = _mapping(base.get("repo"), "release PR base repository")
        _exact(
            base_repo.get("full_name"),
            EXPECTED_REPOSITORY,
            "release PR base repository full_name",
        )
        base_sha = _sha(base.get("sha"), "release PR base SHA")
        head = _mapping(pr.get("head"), "release PR head")
        head_sha = _sha(head.get("sha"), "release PR head SHA")
        head_repo = _mapping(head.get("repo"), "release PR head repository")
        _exact(
            head_repo.get("full_name"),
            EXPECTED_REPOSITORY,
            "release PR head repository full_name",
        )
        if base_sha == head_sha:
            raise MainTrustError("release PR base and head SHAs must differ")
        pr_merge_sha = _sha(pr.get("merge_commit_sha"), "release PR merge commit SHA")
        if pr_merge_sha != checkout:
            raise MainTrustError(
                "release PR merge commit SHA does not match main/tag/checkout"
            )

        commit = _mapping(merge_commit, "release PR merge commit response")
        if _sha(commit.get("sha"), "merge commit response SHA") != pr_merge_sha:
            raise MainTrustError(
                "merge commit response SHA does not match release PR merge_commit_sha"
            )
        parents_value = commit.get("parents")
        if type(parents_value) is not list:
            raise MainTrustError("merge commit parents must be a JSON array")
        if len(parents_value) != 2:
            raise MainTrustError("release PR merge commit must have exactly two parents")
        parent_shas = [
            _sha(
                _mapping(parent, "merge commit parent %d" % (index + 1)).get("sha"),
                "merge commit parent %d SHA" % (index + 1),
            )
            for index, parent in enumerate(parents_value)
        ]
        if parent_shas != [base_sha, head_sha]:
            raise MainTrustError(
                "merge commit parents do not exactly match PR base then PR head"
            )
        pr_summary = {
            "baseCommitSha": base_sha,
            "headCommitSha": head_sha,
            "mergeCommitSha": pr_merge_sha,
            "mergeCommitPolicy": "exactly-two-parents-base-then-head",
            "number": expected_pr_number,
            "parents": parent_shas,
        }

    return {
        "checkoutCommitSha": checkout,
        "checkoutAuthentication": "git-rev-parse-head-revalidated",
        "defaultBranch": EXPECTED_DEFAULT_BRANCH,
        "mainCommitSha": main_sha,
        "refRevalidation": {
            "finalMainCommitSha": final_refs["mainCommitSha"],
            "finalTagCommitSha": final_refs["tagCommitSha"],
            "finalTagObjectSha": final_refs["tagObjectSha"],
        },
        "releasePullRequest": pr_summary,
        "repository": EXPECTED_REPOSITORY,
        "schemaVersion": 1,
        "sourceMode": mode,
        "tag": {
            "name": tag,
            "objectSha": tag_object_sha,
            "objectType": "tag",
            "peeledCommitSha": peeled_sha,
            "peeledType": "commit",
            "ref": "refs/tags/" + tag,
        },
    }


def _gh_api(gh_executable: str, endpoint: str) -> Dict[str, Any]:
    executable = _safe_executable(gh_executable, "gh executable")
    command = [
        executable,
        "api",
        "--hostname",
        "github.com",
        "--method",
        "GET",
        "--header",
        "Accept: application/vnd.github+json",
        "--header",
        "X-GitHub-Api-Version: " + API_VERSION,
        endpoint,
    ]
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MainTrustError("gh api failed for %s: %s" % (endpoint, error)) from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 500:
            detail = detail[:500] + "..."
        raise MainTrustError(
            "gh api failed for %s with exit %d%s"
            % (endpoint, result.returncode, ": " + detail if detail else "")
        )
    return _parse_json(result.stdout, "gh api " + endpoint)


def query_api_documents(
    *, gh_executable: str, tag: str, release_pr_number: Optional[int]
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Fetch fixed GitHub REST endpoints and re-fetch mutable refs at the end."""

    if not TAG_RE.fullmatch(tag):
        raise MainTrustError("tag must be canonical vMAJOR.MINOR.PATCH")
    number = (
        _positive_integer(release_pr_number, "release PR number argument")
        if release_pr_number is not None
        else None
    )
    root = "repos/" + EXPECTED_REPOSITORY
    repository = _gh_api(gh_executable, root)
    main_ref = _gh_api(gh_executable, root + "/git/ref/heads/main")
    tag_ref = _gh_api(gh_executable, root + "/git/ref/tags/" + tag)
    tag_target = _mapping(tag_ref.get("object"), "tag ref object")
    tag_object_sha = _sha(tag_target.get("sha"), "annotated tag object SHA")
    tag_object = _gh_api(gh_executable, root + "/git/tags/" + tag_object_sha)

    release_pr = None
    merge_commit = None
    if number is not None:
        release_pr = _gh_api(gh_executable, root + "/pulls/" + str(number))
        merge_sha = _sha(
            release_pr.get("merge_commit_sha"), "release PR merge commit SHA"
        )
        merge_commit = _gh_api(gh_executable, root + "/git/commits/" + merge_sha)

    # Close the API-read TOCTOU window: the mutable refs are fetched again only
    # after all optional PR/commit evidence has been obtained.  The final tag
    # object is addressed through the object SHA returned by that second ref.
    final_main_ref = _gh_api(gh_executable, root + "/git/ref/heads/main")
    final_tag_ref = _gh_api(gh_executable, root + "/git/ref/tags/" + tag)
    final_tag_target = _mapping(final_tag_ref.get("object"), "final tag ref object")
    final_tag_object_sha = _sha(
        final_tag_target.get("sha"), "final annotated tag object SHA"
    )
    final_tag_object = _gh_api(
        gh_executable, root + "/git/tags/" + final_tag_object_sha
    )

    return {
        "repository": repository,
        "main_ref": main_ref,
        "tag_ref": tag_ref,
        "tag_object": tag_object,
        "final_main_ref": final_main_ref,
        "final_tag_ref": final_tag_ref,
        "final_tag_object": final_tag_object,
        "release_pr": release_pr,
        "merge_commit": merge_commit,
    }


def load_saved_api_documents(
    api_directory: Path, release_pr_number: Optional[int]
) -> Dict[str, Optional[Dict[str, Any]]]:
    if api_directory.is_symlink() or not api_directory.is_dir():
        raise MainTrustError("API directory must be a regular directory: " + str(api_directory))
    names = {
        "repository": "repository.json",
        "main_ref": "main-ref.json",
        "tag_ref": "tag-ref.json",
        "tag_object": "tag-object.json",
        "final_main_ref": "main-ref-final.json",
        "final_tag_ref": "tag-ref-final.json",
        "final_tag_object": "tag-object-final.json",
    }
    documents: Dict[str, Optional[Dict[str, Any]]] = {
        key: _load_json(api_directory / name, name) for key, name in names.items()
    }
    documents["release_pr"] = None
    documents["merge_commit"] = None
    if release_pr_number is not None:
        documents["release_pr"] = _load_json(
            api_directory / "release-pr.json", "release-pr.json"
        )
        documents["merge_commit"] = _load_json(
            api_directory / "merge-commit.json", "merge-commit.json"
        )
    return documents


def _required_api_document(
    documents: Mapping[str, Optional[Dict[str, Any]]], key: str
) -> Dict[str, Any]:
    value = documents.get(key)
    if value is None:
        raise MainTrustError("required API document is missing: " + key)
    return value


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def _write_new_file(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise MainTrustError("cannot create JSON output because it exists: " + str(path))
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise MainTrustError(
            "JSON output parent must be a regular directory: " + str(parent)
        )
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + path.name + ".atomic-main-trust-", dir=str(parent)
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A same-directory hard link publishes a fully-written inode while
        # preserving create-new semantics on both Windows and POSIX.
        os.link(temporary_name, path)
    except OSError as error:
        raise MainTrustError("cannot create JSON output %s: %s" % (path, error)) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def _github_output_lines(summary: Mapping[str, Any], canonical: bytes) -> bytes:
    tag = _mapping(summary.get("tag"), "summary tag")
    values = [
        ("release_trust_json", canonical.rstrip(b"\n").decode("ascii")),
        ("release_trust_sha256", hashlib.sha256(canonical).hexdigest()),
        ("source_mode", _source_mode(summary.get("sourceMode"))),
        ("repository", _string(summary.get("repository"), "summary repository")),
        ("default_branch", _string(summary.get("defaultBranch"), "summary default branch")),
        ("main_commit_sha", _sha(summary.get("mainCommitSha"), "summary main SHA")),
        (
            "checkout_commit_sha",
            _sha(summary.get("checkoutCommitSha"), "summary checkout SHA"),
        ),
        ("tag_name", _string(tag.get("name"), "summary tag name")),
        ("tag_object_sha", _sha(tag.get("objectSha"), "summary tag object SHA")),
        (
            "tag_commit_sha",
            _sha(tag.get("peeledCommitSha"), "summary tag commit SHA"),
        ),
    ]
    pr = summary.get("releasePullRequest")
    if pr is not None:
        pr_value = _mapping(pr, "summary release PR")
        values.extend(
            [
                (
                    "release_pr_number",
                    str(_positive_integer(pr_value.get("number"), "summary PR number")),
                ),
                (
                    "release_pr_merge_commit_sha",
                    _sha(pr_value.get("mergeCommitSha"), "summary PR merge SHA"),
                ),
            ]
        )
    return "".join("%s=%s\n" % item for item in values).encode("ascii")


def _read_github_output(path: Path) -> Tuple[bytes, Optional[os.stat_result]]:
    try:
        information = path.lstat()
    except FileNotFoundError:
        return b"", None
    except OSError as error:
        raise MainTrustError("cannot inspect GitHub output %s: %s" % (path, error)) from error
    if stat.S_ISLNK(information.st_mode) or not stat.S_ISREG(information.st_mode):
        raise MainTrustError(
            "GitHub output must be a regular, non-symlink file: " + str(path)
        )
    if information.st_size > MAX_GITHUB_OUTPUT_BYTES:
        raise MainTrustError("existing GitHub output exceeds the size limit")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise MainTrustError("cannot read GitHub output %s: %s" % (path, error)) from error
    if len(payload) != information.st_size:
        raise MainTrustError("GitHub output changed while it was being read")
    return payload, information


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
    )


def _append_github_outputs(path: Path, payload: bytes) -> None:
    existing, initial_state = _read_github_output(path)
    combined = existing + payload
    if len(combined) > MAX_GITHUB_OUTPUT_BYTES:
        raise MainTrustError("resulting GitHub output exceeds the size limit")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise MainTrustError(
            "GitHub output parent must be a regular directory: " + str(parent)
        )
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="." + path.name + ".atomic-main-trust-", dir=str(parent)
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(combined)
            stream.flush()
            os.fsync(stream.fileno())
        if initial_state is None:
            if path.exists() or path.is_symlink():
                raise MainTrustError(
                    "GitHub output appeared while the atomic append was staged"
                )
        else:
            try:
                current_state = path.lstat()
            except OSError as error:
                raise MainTrustError(
                    "GitHub output changed while the atomic append was staged"
                ) from error
            if not _same_file_state(initial_state, current_state):
                raise MainTrustError(
                    "GitHub output changed while the atomic append was staged"
                )
            os.chmod(temporary_name, stat.S_IMODE(initial_state.st_mode))
        os.replace(temporary_name, path)
        temporary_name = ""
    except MainTrustError:
        raise
    except OSError as error:
        raise MainTrustError(
            "cannot atomically append GitHub output %s: %s" % (path, error)
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass


def _absolute_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _publish_outputs(
    *,
    json_output: Optional[Path],
    github_output: Optional[Path],
    json_payload: bytes,
    github_payload: bytes,
) -> None:
    if (
        json_output is not None
        and github_output is not None
        and _absolute_key(json_output) == _absolute_key(github_output)
    ):
        raise MainTrustError("JSON output and GitHub output must be different files")
    json_created = False
    try:
        if json_output is not None:
            _write_new_file(json_output, json_payload)
            json_created = True
        if github_output is not None:
            _append_github_outputs(github_output, github_payload)
    except MainTrustError as error:
        if json_created and json_output is not None:
            try:
                json_output.unlink()
            except OSError as rollback_error:
                raise MainTrustError(
                    "%s; additionally failed to roll back JSON output: %s"
                    % (error, rollback_error)
                ) from error
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=EXPECTED_REPOSITORY)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--checkout-sha")
    parser.add_argument("--release-pr", type=int)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--api-directory", "--api-dir", type=Path)
    source.add_argument("--query-gh", action="store_true")
    parser.add_argument("--gh-executable", default="gh")
    parser.add_argument("--git-executable", default="git")
    parser.add_argument("--require-online", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        source_mode = "online" if args.query_gh else "saved"
        if args.require_online and source_mode != "online":
            raise MainTrustError("--require-online rejects saved API evidence")
        if args.release_pr is not None:
            _positive_integer(args.release_pr, "release PR number argument")
        checkout_sha = authenticate_checkout(
            repository_root=args.repository_root,
            git_executable=args.git_executable,
            expected_checkout_sha=args.checkout_sha,
        )
        if args.query_gh:
            documents = query_api_documents(
                gh_executable=args.gh_executable,
                tag=args.tag,
                release_pr_number=args.release_pr,
            )
        else:
            documents = load_saved_api_documents(args.api_directory, args.release_pr)
        summary = validate_release_trust(
            repository=_required_api_document(documents, "repository"),
            main_ref=_required_api_document(documents, "main_ref"),
            tag_ref=_required_api_document(documents, "tag_ref"),
            tag_object=_required_api_document(documents, "tag_object"),
            final_main_ref=_required_api_document(documents, "final_main_ref"),
            final_tag_ref=_required_api_document(documents, "final_tag_ref"),
            final_tag_object=_required_api_document(documents, "final_tag_object"),
            expected_repository=args.repository,
            tag=args.tag,
            checkout_sha=checkout_sha,
            source_mode=source_mode,
            release_pr_number=args.release_pr,
            release_pr=documents["release_pr"],
            merge_commit=documents["merge_commit"],
        )
        final_checkout_sha = authenticate_checkout(
            repository_root=args.repository_root,
            git_executable=args.git_executable,
            expected_checkout_sha=checkout_sha,
        )
        if final_checkout_sha != checkout_sha:
            raise MainTrustError("checkout HEAD changed during trust validation")
        payload = canonical_json_bytes(summary)
        _publish_outputs(
            json_output=args.json_output,
            github_output=args.github_output,
            json_payload=payload,
            github_payload=_github_output_lines(summary, payload),
        )
        sys.stdout.buffer.write(payload)
        return 0
    except MainTrustError as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
