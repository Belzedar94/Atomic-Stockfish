from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "atomic_release_main_trust.py"
SPEC = importlib.util.spec_from_file_location("atomic_release_main_trust", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
trust = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trust)

REPOSITORY = "Belzedar94/Atomic-Stockfish"
TAG = "v1.0.0"
CHECKOUT = "a" * 40
TAG_OBJECT = "b" * 40
BASE = "c" * 40
HEAD = "d" * 40


def api_documents(
    with_pr: bool = False, *, checkout: str = CHECKOUT
) -> dict[str, object]:
    documents: dict[str, object] = {
        "repository": {"full_name": REPOSITORY, "default_branch": "main"},
        "main_ref": {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": checkout},
        },
        "tag_ref": {
            "ref": "refs/tags/" + TAG,
            "object": {"type": "tag", "sha": TAG_OBJECT},
        },
        "tag_object": {
            "sha": TAG_OBJECT,
            "tag": TAG,
            "message": "Atomic-Stockfish 1.0.0",
            "tagger": {
                "name": "Atomic release",
                "email": "release@example.invalid",
                "date": "2026-07-16T12:00:00Z",
            },
            "object": {"type": "commit", "sha": checkout},
        },
        "final_main_ref": {
            "ref": "refs/heads/main",
            "object": {"type": "commit", "sha": checkout},
        },
        "final_tag_ref": {
            "ref": "refs/tags/" + TAG,
            "object": {"type": "tag", "sha": TAG_OBJECT},
        },
        "final_tag_object": {
            "sha": TAG_OBJECT,
            "tag": TAG,
            "message": "Atomic-Stockfish 1.0.0",
            "tagger": {
                "name": "Atomic release",
                "email": "release@example.invalid",
                "date": "2026-07-16T12:00:00Z",
            },
            "object": {"type": "commit", "sha": checkout},
        },
        "release_pr": None,
        "merge_commit": None,
    }
    if with_pr:
        documents["release_pr"] = {
            "number": 44,
            "state": "closed",
            "merged": True,
            "merged_at": "2026-07-16T12:00:00Z",
            "merge_commit_sha": checkout,
            "base": {
                "ref": "main",
                "sha": BASE,
                "repo": {"full_name": REPOSITORY},
            },
            "head": {
                "ref": "agent/release-1-0-prep",
                "sha": HEAD,
                "repo": {"full_name": REPOSITORY},
            },
        }
        documents["merge_commit"] = {
            "sha": checkout,
            "parents": [{"sha": BASE}, {"sha": HEAD}],
        }
    return documents


def validate(
    documents: dict[str, object],
    *,
    with_pr: bool = False,
    checkout: str = CHECKOUT,
    source_mode: str = "saved",
) -> dict[str, object]:
    return trust.validate_release_trust(
        repository=documents["repository"],
        main_ref=documents["main_ref"],
        tag_ref=documents["tag_ref"],
        tag_object=documents["tag_object"],
        final_main_ref=documents["final_main_ref"],
        final_tag_ref=documents["final_tag_ref"],
        final_tag_object=documents["final_tag_object"],
        expected_repository=REPOSITORY,
        tag=TAG,
        checkout_sha=checkout,
        source_mode=source_mode,
        release_pr_number=44 if with_pr else None,
        release_pr=documents["release_pr"],
        merge_commit=documents["merge_commit"],
    )


def write_saved_api(directory: Path, documents: dict[str, object], with_pr: bool) -> None:
    directory.mkdir()
    names = {
        "repository": "repository.json",
        "main_ref": "main-ref.json",
        "tag_ref": "tag-ref.json",
        "tag_object": "tag-object.json",
        "final_main_ref": "main-ref-final.json",
        "final_tag_ref": "tag-ref-final.json",
        "final_tag_object": "tag-object-final.json",
    }
    if with_pr:
        names.update(
            {"release_pr": "release-pr.json", "merge_commit": "merge-commit.json"}
        )
    for key, name in names.items():
        (directory / name).write_bytes(json.dumps(documents[key]).encode("utf-8"))


def repository_checkout() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD^{commit}"],
        text=True,
    ).strip()


def test_valid_main_and_annotated_tag_return_canonical_trust_summary() -> None:
    summary = validate(api_documents())

    assert summary == {
        "checkoutCommitSha": CHECKOUT,
        "checkoutAuthentication": "git-rev-parse-head-revalidated",
        "defaultBranch": "main",
        "mainCommitSha": CHECKOUT,
        "refRevalidation": {
            "finalMainCommitSha": CHECKOUT,
            "finalTagCommitSha": CHECKOUT,
            "finalTagObjectSha": TAG_OBJECT,
        },
        "releasePullRequest": None,
        "repository": REPOSITORY,
        "schemaVersion": 1,
        "sourceMode": "saved",
        "tag": {
            "name": TAG,
            "objectSha": TAG_OBJECT,
            "objectType": "tag",
            "peeledCommitSha": CHECKOUT,
            "peeledType": "commit",
            "ref": "refs/tags/" + TAG,
        },
    }
    payload = trust.canonical_json_bytes(summary)
    assert payload.endswith(b"\n")
    assert b"\r" not in payload
    assert payload == (
        json.dumps(summary, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def test_valid_release_pr_authenticates_merge_commit_and_ordered_parents() -> None:
    summary = validate(api_documents(with_pr=True), with_pr=True)

    assert summary["releasePullRequest"] == {
        "baseCommitSha": BASE,
        "headCommitSha": HEAD,
        "mergeCommitSha": CHECKOUT,
        "mergeCommitPolicy": "exactly-two-parents-base-then-head",
        "number": 44,
        "parents": [BASE, HEAD],
    }


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("repository", "full_name"), "Other/Repository", "full_name mismatch"),
        (("repository", "default_branch"), "develop", "default_branch mismatch"),
        (("main_ref", "ref"), "refs/heads/release", "main ref mismatch"),
        (("main_ref", "object", "type"), "tag", "main ref object type mismatch"),
        (("main_ref", "object", "sha"), "A" * 40, "lowercase 40-hex"),
        (("main_ref", "object", "sha"), "e" * 40, "main ref commit SHA"),
        (("tag_ref", "ref"), "refs/tags/v1.0.1", "tag ref mismatch"),
        (("tag_ref", "object", "type"), "commit", "tag ref object type mismatch"),
        (("tag_ref", "object", "sha"), "short", "lowercase 40-hex"),
        (("tag_object", "sha"), "f" * 40, "does not match the exact tag ref"),
        (("tag_object", "tag"), "v1.0.1", "annotated tag name mismatch"),
        (("tag_object", "object", "type"), "tree", "peeled object type mismatch"),
        (("tag_object", "object", "sha"), "e" * 40, "peeled commit SHA"),
    ],
)
def test_repository_main_and_tag_mismatches_fail_closed(
    path: tuple[str, ...], value: object, message: str
) -> None:
    documents = api_documents()
    target: object = documents
    for key in path[:-1]:
        assert isinstance(target, dict)
        target = target[key]
    assert isinstance(target, dict)
    target[path[-1]] = value

    with pytest.raises(trust.MainTrustError, match=message):
        validate(documents)


@pytest.mark.parametrize(
    ("key", "mutation", "message"),
    [
        (
            "final_main_ref",
            lambda value: value["object"].update(sha="e" * 40),
            "final main ref commit SHA",
        ),
        (
            "final_tag_ref",
            lambda value: value["object"].update(sha="f" * 40),
            "final tag response SHA does not match",
        ),
        (
            "final_tag_object",
            lambda value: value["object"].update(sha="e" * 40),
            "final tag peeled commit SHA",
        ),
    ],
)
def test_final_main_and_tag_revalidation_closes_toctou(
    key: str, mutation, message: str
) -> None:
    documents = api_documents()
    mutation(documents[key])

    with pytest.raises(trust.MainTrustError, match=message):
        validate(documents)


def test_coherent_tag_retarget_during_validation_is_rejected() -> None:
    documents = api_documents()
    replacement_object = "f" * 40
    documents["final_tag_ref"]["object"]["sha"] = replacement_object
    documents["final_tag_object"]["sha"] = replacement_object

    with pytest.raises(trust.MainTrustError, match="changed during trust validation"):
        validate(documents)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda pr, commit: pr.update(state="open"), "state mismatch"),
        (lambda pr, commit: pr.update(merged=False), "not merged"),
        (lambda pr, commit: pr.update(merged="true"), "not merged"),
        (lambda pr, commit: pr.update(number=True), "positive JSON integer"),
        (lambda pr, commit: pr.update(merge_commit_sha="e" * 40), "does not match"),
        (lambda pr, commit: pr["base"].update(ref="release"), "base ref mismatch"),
        (
            lambda pr, commit: pr["base"]["repo"].update(full_name="Fork/Atomic"),
            "base repository full_name mismatch",
        ),
        (
            lambda pr, commit: pr["head"]["repo"].update(full_name="Fork/Atomic"),
            "head repository full_name mismatch",
        ),
        (lambda pr, commit: commit.update(sha="e" * 40), "response SHA does not match"),
        (lambda pr, commit: commit.update(parents=[{"sha": BASE}]), "exactly two"),
        (
            lambda pr, commit: commit.update(
                parents=[{"sha": BASE}, {"sha": HEAD}, {"sha": "e" * 40}]
            ),
            "exactly two",
        ),
        (
            lambda pr, commit: commit.update(
                parents=[{"sha": HEAD}, {"sha": BASE}]
            ),
            "base then PR head",
        ),
    ],
)
def test_unmerged_or_incoherent_release_pr_fails_closed(mutation, message: str) -> None:
    documents = api_documents(with_pr=True)
    mutation(documents["release_pr"], documents["merge_commit"])

    with pytest.raises(trust.MainTrustError, match=message):
        validate(documents, with_pr=True)


def test_optional_release_pr_inputs_are_all_or_nothing() -> None:
    documents = api_documents(with_pr=True)
    documents["merge_commit"] = None

    with pytest.raises(trust.MainTrustError, match="must be supplied together"):
        validate(documents, with_pr=True)


def test_repository_argument_and_release_tag_are_fixed_and_safe() -> None:
    documents = api_documents()
    kwargs = dict(
        repository=documents["repository"],
        main_ref=documents["main_ref"],
        tag_ref=documents["tag_ref"],
        tag_object=documents["tag_object"],
        final_main_ref=documents["final_main_ref"],
        final_tag_ref=documents["final_tag_ref"],
        final_tag_object=documents["final_tag_object"],
        checkout_sha=CHECKOUT,
        source_mode="saved",
    )
    with pytest.raises(trust.MainTrustError, match="must be exactly"):
        trust.validate_release_trust(
            **kwargs, expected_repository="Fork/Atomic-Stockfish", tag=TAG
        )
    with pytest.raises(trust.MainTrustError, match="canonical vMAJOR"):
        trust.validate_release_trust(
            **kwargs,
            expected_repository=REPOSITORY,
            tag="v1.0.0/../../heads/main",
        )


def test_checkout_is_authenticated_from_exact_git_repository_root() -> None:
    checkout = repository_checkout()

    assert trust.authenticate_checkout(
        repository_root=ROOT,
        git_executable="git",
        expected_checkout_sha=checkout,
    ) == checkout
    with pytest.raises(trust.MainTrustError, match="does not match expected"):
        trust.authenticate_checkout(
            repository_root=ROOT,
            git_executable="git",
            expected_checkout_sha="e" * 40,
        )
    with pytest.raises(trust.MainTrustError, match="does not exactly match"):
        trust.authenticate_checkout(
            repository_root=ROOT / "tests",
            git_executable="git",
        )


def test_source_mode_is_explicit_and_fail_closed() -> None:
    summary = validate(api_documents(), source_mode="online")
    assert summary["sourceMode"] == "online"

    documents = api_documents()
    with pytest.raises(trust.MainTrustError, match="online.*saved"):
        trust.validate_release_trust(
            repository=documents["repository"],
            main_ref=documents["main_ref"],
            tag_ref=documents["tag_ref"],
            tag_object=documents["tag_object"],
            final_main_ref=documents["final_main_ref"],
            final_tag_ref=documents["final_tag_ref"],
            final_tag_object=documents["final_tag_object"],
            expected_repository=REPOSITORY,
            tag=TAG,
            checkout_sha=CHECKOUT,
            source_mode="cached",
        )


def test_require_online_rejects_saved_evidence_before_writing_outputs(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--tag",
            TAG,
            "--repository-root",
            str(ROOT),
            "--api-directory",
            str(tmp_path / "not-used"),
            "--require-online",
            "--json-output",
            str(tmp_path / "trust.json"),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == b""
    assert b"rejects saved API evidence" in result.stderr
    assert not (tmp_path / "trust.json").exists()


def test_saved_api_cli_emits_one_canonical_line_file_and_github_outputs(
    tmp_path: Path,
) -> None:
    checkout = repository_checkout()
    documents = api_documents(with_pr=True, checkout=checkout)
    api_dir = tmp_path / "api"
    write_saved_api(api_dir, documents, with_pr=True)
    json_output = tmp_path / "trust.json"
    github_output = tmp_path / "github-output.txt"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repository",
            REPOSITORY,
            "--tag",
            TAG,
            "--repository-root",
            str(ROOT),
            "--checkout-sha",
            checkout,
            "--release-pr",
            "44",
            "--api-directory",
            str(api_dir),
            "--json-output",
            str(json_output),
            "--github-output",
            str(github_output),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode()
    assert result.stderr == b""
    assert result.stdout == json_output.read_bytes()
    assert result.stdout == trust.canonical_json_bytes(json.loads(result.stdout))
    outputs = github_output.read_bytes()
    assert b"\r" not in outputs
    assert b"release_trust_json=" + result.stdout.rstrip(b"\n") + b"\n" in outputs
    assert (
        "release_trust_sha256=" + hashlib.sha256(result.stdout).hexdigest() + "\n"
    ).encode() in outputs
    assert ("repository=" + REPOSITORY + "\n").encode() in outputs
    assert b"source_mode=saved\n" in outputs
    assert b"default_branch=main\n" in outputs
    assert ("main_commit_sha=" + checkout + "\n").encode() in outputs
    assert b"release_pr_number=44\n" in outputs
    assert ("release_pr_merge_commit_sha=" + checkout + "\n").encode() in outputs


def test_failed_saved_api_cli_emits_no_success_payload_or_new_outputs(
    tmp_path: Path,
) -> None:
    checkout = repository_checkout()
    documents = api_documents(checkout=checkout)
    documents["repository"]["default_branch"] = "develop"
    api_dir = tmp_path / "api"
    write_saved_api(api_dir, documents, with_pr=False)
    json_output = tmp_path / "trust.json"
    github_output = tmp_path / "github-output.txt"
    github_output.write_bytes(b"existing=1\n")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--tag",
            TAG,
            "--repository-root",
            str(ROOT),
            "--checkout-sha",
            checkout,
            "--api-dir",
            str(api_dir),
            "--json-output",
            str(json_output),
            "--github-output",
            str(github_output),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == b""
    assert b"default_branch mismatch" in result.stderr
    assert not json_output.exists()
    assert github_output.read_bytes() == b"existing=1\n"


def test_saved_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    documents = api_documents()
    api_dir = tmp_path / "api"
    write_saved_api(api_dir, documents, with_pr=False)
    (api_dir / "repository.json").write_text(
        '{"full_name":"Belzedar94/Atomic-Stockfish",'
        '"full_name":"Belzedar94/Atomic-Stockfish","default_branch":"main"}',
        encoding="utf-8",
    )

    with pytest.raises(trust.MainTrustError, match="duplicate JSON key"):
        trust.load_saved_api_documents(api_dir, None)


def test_saved_json_rejects_non_json_nan_constant(tmp_path: Path) -> None:
    documents = api_documents()
    api_dir = tmp_path / "api"
    write_saved_api(api_dir, documents, with_pr=False)
    (api_dir / "repository.json").write_text(
        '{"full_name":"Belzedar94/Atomic-Stockfish",'
        '"default_branch":"main","unexpected":NaN}',
        encoding="utf-8",
    )

    with pytest.raises(trust.MainTrustError, match="non-JSON numeric constant"):
        trust.load_saved_api_documents(api_dir, None)


def test_gh_queries_use_fixed_endpoints_argument_vector_and_no_shell(monkeypatch) -> None:
    documents = api_documents()
    root = "repos/" + REPOSITORY
    responses = {
        root: documents["repository"],
        root + "/git/ref/heads/main": documents["main_ref"],
        root + "/git/ref/tags/" + TAG: documents["tag_ref"],
        root + "/git/tags/" + TAG_OBJECT: documents["tag_object"],
    }
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert isinstance(command, list)
        assert kwargs == {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "timeout": trust.COMMAND_TIMEOUT_SECONDS,
            "check": False,
        }
        endpoint = command[-1]
        return subprocess.CompletedProcess(
            command, 0, json.dumps(responses[endpoint]).encode(), b""
        )

    monkeypatch.setattr(trust.subprocess, "run", fake_run)
    fetched = trust.query_api_documents(
        gh_executable="gh; this-is-one-literal-argument", tag=TAG, release_pr_number=None
    )

    assert fetched["repository"] == documents["repository"]
    assert [command[-1] for command in commands] == [
        root,
        root + "/git/ref/heads/main",
        root + "/git/ref/tags/" + TAG,
        root + "/git/tags/" + TAG_OBJECT,
        root + "/git/ref/heads/main",
        root + "/git/ref/tags/" + TAG,
        root + "/git/tags/" + TAG_OBJECT,
    ]
    assert all(command[0] == "gh; this-is-one-literal-argument" for command in commands)
    assert all("--hostname" in command and "github.com" in command for command in commands)


def test_online_query_with_pr_authenticates_merge_commit_then_rechecks_refs(
    monkeypatch,
) -> None:
    documents = api_documents(with_pr=True)
    root = "repos/" + REPOSITORY
    endpoints = {
        root: documents["repository"],
        root + "/git/ref/heads/main": documents["main_ref"],
        root + "/git/ref/tags/" + TAG: documents["tag_ref"],
        root + "/git/tags/" + TAG_OBJECT: documents["tag_object"],
        root + "/pulls/44": documents["release_pr"],
        root + "/git/commits/" + CHECKOUT: documents["merge_commit"],
    }
    calls: list[str] = []

    def fake_run(command, **kwargs):
        endpoint = command[-1]
        calls.append(endpoint)
        return subprocess.CompletedProcess(
            command, 0, json.dumps(endpoints[endpoint]).encode(), b""
        )

    monkeypatch.setattr(trust.subprocess, "run", fake_run)
    fetched = trust.query_api_documents(
        gh_executable="gh", tag=TAG, release_pr_number=44
    )
    summary = validate(fetched, with_pr=True, source_mode="online")

    assert summary["sourceMode"] == "online"
    assert summary["releasePullRequest"]["mergeCommitPolicy"] == (
        "exactly-two-parents-base-then-head"
    )
    assert calls == [
        root,
        root + "/git/ref/heads/main",
        root + "/git/ref/tags/" + TAG,
        root + "/git/tags/" + TAG_OBJECT,
        root + "/pulls/44",
        root + "/git/commits/" + CHECKOUT,
        root + "/git/ref/heads/main",
        root + "/git/ref/tags/" + TAG,
        root + "/git/tags/" + TAG_OBJECT,
    ]


def test_online_open_release_pr_fails_closed(monkeypatch) -> None:
    documents = api_documents(with_pr=True)
    documents["release_pr"]["state"] = "open"
    documents["release_pr"]["merged"] = False
    root = "repos/" + REPOSITORY
    endpoints = {
        root: documents["repository"],
        root + "/git/ref/heads/main": documents["main_ref"],
        root + "/git/ref/tags/" + TAG: documents["tag_ref"],
        root + "/git/tags/" + TAG_OBJECT: documents["tag_object"],
        root + "/pulls/44": documents["release_pr"],
        root + "/git/commits/" + CHECKOUT: documents["merge_commit"],
    }

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, json.dumps(endpoints[command[-1]]).encode(), b""
        )

    monkeypatch.setattr(trust.subprocess, "run", fake_run)
    fetched = trust.query_api_documents(
        gh_executable="gh", tag=TAG, release_pr_number=44
    )

    with pytest.raises(trust.MainTrustError, match="state mismatch"):
        validate(fetched, with_pr=True, source_mode="online")


def test_online_github_404_is_fatal_and_never_falls_back(monkeypatch) -> None:
    documents = api_documents()
    root = "repos/" + REPOSITORY
    calls: list[str] = []

    def fake_run(command, **kwargs):
        endpoint = command[-1]
        calls.append(endpoint)
        if endpoint == root + "/git/ref/tags/" + TAG:
            return subprocess.CompletedProcess(
                command, 1, b"", b"gh: Not Found (HTTP 404)\n"
            )
        response = (
            documents["repository"]
            if endpoint == root
            else documents["main_ref"]
        )
        return subprocess.CompletedProcess(
            command, 0, json.dumps(response).encode(), b""
        )

    monkeypatch.setattr(trust.subprocess, "run", fake_run)

    with pytest.raises(trust.MainTrustError, match=r"Not Found \(HTTP 404\)"):
        trust.query_api_documents(
            gh_executable="gh", tag=TAG, release_pr_number=None
        )
    assert calls == [
        root,
        root + "/git/ref/heads/main",
        root + "/git/ref/tags/" + TAG,
    ]


def test_online_gh_process_failure_is_fatal(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, trust.COMMAND_TIMEOUT_SECONDS)

    monkeypatch.setattr(trust.subprocess, "run", fake_run)
    with pytest.raises(trust.MainTrustError, match="gh api failed"):
        trust.query_api_documents(
            gh_executable="gh", tag=TAG, release_pr_number=None
        )


def test_json_output_refuses_to_overwrite_an_existing_result(tmp_path: Path) -> None:
    target = tmp_path / "trust.json"
    target.write_bytes(b"old\n")

    with pytest.raises(trust.MainTrustError, match="cannot create JSON output"):
        trust._write_new_file(target, b"new\n")
    assert target.read_bytes() == b"old\n"


def test_output_transaction_rolls_back_json_if_atomic_github_append_fails(
    tmp_path: Path, monkeypatch
) -> None:
    json_output = tmp_path / "trust.json"
    github_output = tmp_path / "github-output.txt"
    github_output.write_bytes(b"existing=1\n")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(trust.os, "replace", fail_replace)
    with pytest.raises(trust.MainTrustError, match="atomically append"):
        trust._publish_outputs(
            json_output=json_output,
            github_output=github_output,
            json_payload=b'{"trusted":true}\n',
            github_payload=b"trusted=true\n",
        )

    assert not json_output.exists()
    assert github_output.read_bytes() == b"existing=1\n"
    assert list(tmp_path.glob("*.atomic-main-trust-*")) == []


def test_atomic_github_append_preserves_existing_outputs(tmp_path: Path) -> None:
    target = tmp_path / "github-output.txt"
    target.write_bytes(b"existing=1\n")

    trust._append_github_outputs(target, b"trusted=true\n")

    assert target.read_bytes() == b"existing=1\ntrusted=true\n"


def test_output_transaction_rejects_same_destination(tmp_path: Path) -> None:
    target = tmp_path / "outputs.txt"

    with pytest.raises(trust.MainTrustError, match="must be different files"):
        trust._publish_outputs(
            json_output=target,
            github_output=target,
            json_payload=b"{}\n",
            github_payload=b"trusted=true\n",
        )
    assert not target.exists()


def test_saved_api_and_github_output_limits_are_enforced(
    tmp_path: Path, monkeypatch
) -> None:
    documents = api_documents()
    api_dir = tmp_path / "api"
    write_saved_api(api_dir, documents, with_pr=False)
    monkeypatch.setattr(trust, "MAX_API_RESPONSE_BYTES", 8)
    with pytest.raises(trust.MainTrustError, match="size limit"):
        trust.load_saved_api_documents(api_dir, None)

    github_output = tmp_path / "github-output.txt"
    github_output.write_bytes(b"12345678")
    monkeypatch.setattr(trust, "MAX_GITHUB_OUTPUT_BYTES", 10)
    with pytest.raises(trust.MainTrustError, match="resulting.*size limit"):
        trust._append_github_outputs(github_output, b"456")
    assert github_output.read_bytes() == b"12345678"


def test_release_pr_number_has_a_reasonable_upper_bound() -> None:
    with pytest.raises(trust.MainTrustError, match="no greater than"):
        trust.query_api_documents(
            gh_executable="gh",
            tag=TAG,
            release_pr_number=trust.MAX_PR_NUMBER + 1,
        )
