import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
import psutil
import pytest

from scripts import atomic_process_containment as CONTAINMENT
from scripts import atomic_release_exact_tag_gates as EXACT

BENCH: Any = importlib.import_module("tests.atomic_bench_compare")
ORCHESTRATOR: Any = importlib.import_module("tests.run_atomic_release_exact_tag_gate")


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SOURCE = ROOT / "schemas" / "atomic-release-exact-tag-gates-v1.json"
PRODUCTIVE_PLAN = ROOT / "scripts" / "atomic-release-exact-tag-plan-v1.json"
RELEASE_INVENTORY = ROOT / "docs" / "atomic" / "release-1.0-inventory.json"
WINDOWS_CONTAINMENT_REASON = "exact-tag containment requires a Windows Job Object"


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def make_clean_repo(root: Path, filename: str = "tracked.txt") -> tuple[str, str]:
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "atomic-tests@example.invalid")
    git(root, "config", "user.name", "Atomic tests")
    git(root, "config", "core.autocrlf", "false")
    (root / filename).write_text("tracked\n", encoding="ascii")
    git(root, "add", filename)
    git(root, "commit", "--quiet", "-m", "fixture")
    return git(root, "rev-parse", "HEAD"), git(root, "rev-parse", "HEAD^{tree}")


def make_external_inputs(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    root.mkdir()
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    suffixes = {
        "legacy_net": ".nnue",
        "fairy_h5_oracle": ".exe",
        "syzygy_inventory": ".json",
        "kppppvk_wdl": ".rtbw",
        "kppppvk_dtz": ".rtbz",
        "fairy_bmi2_baseline": ".exe",
    }
    for name in EXACT.FROZEN_EXTERNAL_SHA256:
        path = root / (name + suffixes[name])
        payload = ("authenticated fixture for " + name).encode("ascii")
        path.write_bytes(payload)
        paths[name] = path
        hashes[name] = digest(payload)
    monkeypatch.setattr(EXACT, "FROZEN_EXTERNAL_SHA256", hashes)
    return paths


def make_aux_repositories(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    root.mkdir()
    paths: dict[str, Path] = {}
    commits: dict[str, str] = {}
    for name in EXACT.FROZEN_AUX_REPOSITORY_COMMITS:
        path = root / name
        commit, _ = make_clean_repo(path)
        paths[name] = path
        commits[name] = commit
    monkeypatch.setattr(EXACT, "FROZEN_AUX_REPOSITORY_COMMITS", commits)
    return paths


def canonical_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(EXACT.canonical_json(value))


def command_plan(mode: str = "normal") -> dict[str, Any]:
    gates = []
    for gate_id in EXACT.REQUIRED_GATES:
        receipt = (
            "bench/benchmark.json"
            if gate_id == EXACT.BENCH_GATE
            else f"receipts/{gate_id}.json"
        )
        argv = [
            "@python@",
            "gate_fixture.py",
            "--candidate",
            "@artifact:candidate_bmi2@",
            "--baseline",
            "@external:fairy_bmi2_baseline@",
            "--tools",
            "@repository:tools@",
            "--fairy",
            "@repository:fairy@",
            "--tables",
            "@directory:syzygy_combined@",
            "--mode",
            mode,
        ]
        if gate_id == EXACT.BENCH_GATE:
            argv.extend(["--benchmark-output", EXACT.BENCH_EVIDENCE_PLACEHOLDER])
        if gate_id in EXACT.PIPELINE_PYTHON_GATES:
            argv.extend(["--pipeline-python", EXACT.PIPELINE_PYTHON_PLACEHOLDER])
        gates.append(
            {
                "id": gate_id,
                "timeoutSeconds": EXACT.GATE_TIMEOUT_SECONDS[gate_id],
                "argv": argv,
                "evidence": [receipt],
                "receiptEvidence": receipt,
            }
        )
    return {
        "schemaVersion": 1,
        "project": EXACT.PROJECT,
        "releaseTag": EXACT.RELEASE_TAG,
        "artifacts": ["candidate_bmi2", "native_archive", "pipeline_python"],
        "directories": ["syzygy_combined"],
        "gates": gates,
        "benchmarkEvidence": "bench/benchmark.json",
    }


GATE_FIXTURE = r'''import argparse
import hashlib
import json
import os
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--candidate", type=Path, required=True)
parser.add_argument("--baseline", type=Path, required=True)
parser.add_argument("--tools", type=Path, required=True)
parser.add_argument("--fairy", type=Path, required=True)
parser.add_argument("--tables", type=Path, required=True)
parser.add_argument("--mode", required=True)
parser.add_argument("--benchmark-output", type=Path)
parser.add_argument("--pipeline-python", type=Path)
args = parser.parse_args()
gate = os.environ["ATOMIC_EXACT_GATE_ID"]
root = Path(os.environ["ATOMIC_EXACT_GATE_EVIDENCE_ROOT"])
pipeline_gates = {"legacy-v1-strong-local", "hito5-release", "atomic-bin-v2-strong-local"}
if (args.pipeline_python is not None) != (gate in pipeline_gates):
    raise SystemExit("pipeline Python binding mismatch")
print("real fixture gate executed:", gate, flush=True)

def canonical(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=True, allow_nan=False,
        separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8"))

if args.mode == "fail" and gate == "hito5-release":
    raise SystemExit(7)
if args.mode == "silent" and gate == "hito4-release":
    raise SystemExit(0)
if args.mode == "dirty-tree" and gate == "atomic-bin-v2-strong-local":
    Path("untracked-by-gate.txt").write_text("dirty", encoding="ascii")
if args.mode == "mutate-artifact" and gate == "atomic-bin-v2-strong-local":
    args.candidate.write_bytes(args.candidate.read_bytes() + b"tamper")
if args.mode == "mutate-evidence" and gate == "atomic-bin-v2-strong-local":
    prior = root / "receipts/hito4-release.json"
    prior.write_bytes(prior.read_bytes() + b"tamper")

if gate == "bmi2-vs-fairy":
    if args.benchmark_output != root / "bench/benchmark.json":
        raise SystemExit("benchmark output binding mismatch")
    def artifact(path):
        payload = path.read_bytes()
        return {"fileName": path.name, "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest()}
    candidate_samples = [{"nodes": 1300000, "timeMillis": 1000} for _ in range(5)]
    baseline_samples = [{"nodes": 1300000, "timeMillis": 1250} for _ in range(5)]
    value = {
        "schemaVersion": 1,
        "gate": "bmi2-vs-fairy",
        "metric": "median-nps",
        "candidate": artifact(args.candidate),
        "baseline": artifact(args.baseline),
        "evalFileSha256": os.environ["ATOMIC_EXTERNAL_LEGACY_NET"] and
            hashlib.sha256(Path(os.environ["ATOMIC_EXTERNAL_LEGACY_NET"]).read_bytes()).hexdigest(),
        "corpusSha256": "2738065a8a70d61da46fa3c75f95d645e50e601b43792df0e7b3cc97b1d891a1",
        "positions": 13,
        "nodesPerFen": 100000,
        "threads": 1,
        "hashMb": 64,
        "cpuAffinity": 0,
        "searchTimeoutSeconds": 60,
        "warmups": 1,
        "repetitions": 5,
        "candidateSamples": candidate_samples,
        "baselineSamples": baseline_samples,
        "candidateMedianNps": "1300000.000000",
        "baselineMedianNps": "1040000.000000",
        "ratio": "1.250000",
        "pass": True,
    }
    canonical(args.benchmark_output, value)
else:
    skipped = 1 if args.mode == "skip" and gate == "legacy-v1-strong-local" else 0
    canonical(root / ("receipts/" + gate + ".json"), {
        "schemaVersion": 1,
        "gate": gate,
        "status": "pass",
        "passed": 1,
        "failed": 0,
        "skipped": skipped,
    })
'''


def patched_schema(
    external_hashes: dict[str, str], aux_commits: dict[str, str]
) -> str:
    payload = SCHEMA_SOURCE.read_text(encoding="utf-8")
    original_hashes = {
        "legacy_net": "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6",
        "fairy_h5_oracle": "1ae6d680f03128c8404f31a3f264f28b132b557ed3a91a6445ec563a7a33f623",
        "syzygy_inventory": "3d4b7fd0ab387f4f60da2078f612c9e8890e6026f551aebe8631efc157788f23",
        "kppppvk_wdl": "897a15846a4b027cbd0a31e425fdf0690f68c0bd4e62105cca2055678e2910f9",
        "kppppvk_dtz": "e740168d8cbb0bf662863f278ef470c5d7eb395ece0c8bf80fed004a47991bc6",
        "fairy_bmi2_baseline": "4eacaab40dca84f5a255ea57231f2795d43b5dda85ce50ebba1a1b2937b46331",
    }
    original_commits = {
        "tools": "450049ee7a0ece32694b11f6c55deb7df1d42a84",
        "trainer": "3a19c16fc3d477b1ee7602ccc6510736bc7604cc",
        "fairy": "fb78cb561aa01708338e35b3dc3b65a42149a3c4",
    }
    for name, original in original_hashes.items():
        payload = payload.replace(original, external_hashes[name])
    for name, original in original_commits.items():
        payload = payload.replace(original, aux_commits[name])
    return payload


def make_tagged_repo(
    root: Path,
    *,
    external_hashes: dict[str, str],
    aux_commits: dict[str, str],
    mode: str = "normal",
) -> tuple[str, str]:
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "atomic-tests@example.invalid")
    git(root, "config", "user.name", "Atomic tests")
    git(root, "config", "core.autocrlf", "false")
    (root / "gate_fixture.py").write_text(GATE_FIXTURE, encoding="utf-8")
    schema = root / EXACT.SCHEMA_PATH
    schema.parent.mkdir(parents=True)
    schema.write_text(patched_schema(external_hashes, aux_commits), encoding="utf-8")
    canonical_write(root / "release-plan.json", command_plan(mode))
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "exact tag fixture")
    commit = git(root, "rev-parse", "HEAD")
    git(root, "tag", "-a", EXACT.RELEASE_TAG, "-m", "Atomic 1.0.3 fixture")
    tag_object = git(root, "rev-parse", f"refs/tags/{EXACT.RELEASE_TAG}^{{tag}}")
    return commit, tag_object


def identity_for(repo: Path, commit: str, tag_object: str) -> dict[str, Any]:
    return EXACT.validate_identity(
        repo,
        repository=EXACT.REPOSITORY,
        version=EXACT.RELEASE_VERSION,
        event=EXACT.RELEASE_EVENT,
        ref=EXACT.RELEASE_REF,
        commit=commit,
        tag_object=tag_object,
        run_id="123456789",
        run_attempt=2,
    )


def make_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "normal",
    run: bool = True,
) -> dict[str, Any]:
    if run and os.name != "nt":
        pytest.skip(WINDOWS_CONTAINMENT_REASON)
    external = make_external_inputs(tmp_path / "external", monkeypatch)
    repositories = make_aux_repositories(tmp_path / "aux-repositories", monkeypatch)
    candidate = tmp_path / "Atomic-Stockfish-bmi2.exe"
    candidate.write_bytes(b"authenticated candidate bmi2")
    archive = tmp_path / "Atomic-Stockfish-native.zip"
    archive.write_bytes(b"authenticated native release archive")
    pipeline_python = tmp_path / "python310.exe"
    pipeline_python.write_bytes(b"authenticated pipeline python")
    artifacts = {
        "candidate_bmi2": candidate,
        "native_archive": archive,
        "pipeline_python": pipeline_python,
    }
    table_root = tmp_path / "atomic-tables"
    table_root.mkdir()
    directories = {"syzygy_combined": table_root}
    repo = tmp_path / "repo"
    commit, tag_object = make_tagged_repo(
        repo,
        external_hashes=dict(EXACT.FROZEN_EXTERNAL_SHA256),
        aux_commits=dict(EXACT.FROZEN_AUX_REPOSITORY_COMMITS),
        mode=mode,
    )
    identity = identity_for(repo, commit, tag_object)
    bundle: dict[str, Any] = {
        "repo": repo,
        "plan": "release-plan.json",
        "identity": identity,
        "external": external,
        "artifacts": artifacts,
        "directories": directories,
        "repositories": repositories,
        "evidence": tmp_path / "evidence",
        "manifest": tmp_path / "manifest.json",
    }
    if run:
        bundle["value"] = EXACT.run_gates(
            repo_root=repo,
            plan_path=bundle["plan"],
            evidence_root=bundle["evidence"],
            manifest_path=bundle["manifest"],
            identity=identity,
            external_paths=external,
            artifact_paths=artifacts,
            directory_paths=directories,
            repository_paths=repositories,
        )
    return bundle


def run_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if os.name != "nt":
        pytest.skip(WINDOWS_CONTAINMENT_REASON)
    return EXACT.run_gates(
        repo_root=bundle["repo"],
        plan_path=bundle["plan"],
        evidence_root=bundle["evidence"],
        manifest_path=bundle["manifest"],
        identity=bundle["identity"],
        external_paths=bundle["external"],
        artifact_paths=bundle["artifacts"],
        directory_paths=bundle["directories"],
        repository_paths=bundle["repositories"],
    )


def verify_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return EXACT.verify_manifest(
        repo_root=bundle["repo"],
        plan_path=bundle["plan"],
        manifest_path=bundle["manifest"],
        evidence_root=bundle["evidence"],
        expected_identity=bundle["identity"],
        external_paths=bundle["external"],
        artifact_paths=bundle["artifacts"],
        directory_paths=bundle["directories"],
        repository_paths=bundle["repositories"],
    )


def unseal(path: Path) -> None:
    for entry in [path, *path.rglob("*")]:
        try:
            if entry.is_dir():
                entry.chmod(stat.S_IRWXU)
            else:
                entry.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass


def wait_process_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def cleanup_process_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    processes = [*root.children(recursive=True), root]
    for process in reversed(processes):
        try:
            process.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(processes, timeout=5)


def test_contract_freezes_six_gates_external_hashes_and_aux_commits() -> None:
    assert EXACT.REQUIRED_GATES == (
        "hito4-release",
        "legacy-v1-strong-local",
        "hito5-release",
        "syzygy-real-3-to-6",
        "atomic-bin-v2-strong-local",
        "bmi2-vs-fairy",
    )
    assert EXACT.FROZEN_AUX_REPOSITORY_COMMITS == {
        "tools": "450049ee7a0ece32694b11f6c55deb7df1d42a84",
        "trainer": "3a19c16fc3d477b1ee7602ccc6510736bc7604cc",
        "fairy": "fb78cb561aa01708338e35b3dc3b65a42149a3c4",
    }
    assert EXACT.FROZEN_EXTERNAL_SHA256["legacy_net"] == (
        "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6"
    )
    assert EXACT.FROZEN_EXTERNAL_SHA256["fairy_bmi2_baseline"] == (
        "4eacaab40dca84f5a255ea57231f2795d43b5dda85ce50ebba1a1b2937b46331"
    )


def test_exact_controllers_cannot_import_a_shadow_scripts_package(
    tmp_path: Path,
) -> None:
    shadow = tmp_path / "shadow"
    package = shadow / "scripts"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="ascii")
    marker = tmp_path / "shadow-imported.txt"
    (package / "atomic_process_containment.py").write_text(
        "import os, pathlib\n"
        "pathlib.Path(os.environ['ATOMIC_SHADOW_MARKER']).write_text('bad')\n"
        "raise RuntimeError('shadow containment helper imported')\n",
        encoding="ascii",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(shadow)
    environment["ATOMIC_SHADOW_MARKER"] = str(marker)
    for controller in (
        ROOT / "scripts" / "atomic_release_exact_tag_gates.py",
        ROOT / "tests" / "run_atomic_release_exact_tag_gate.py",
    ):
        result = subprocess.run(
            [sys.executable, str(controller), "--help"],
            cwd=shadow,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert not marker.exists()
    helper = (ROOT / "scripts" / "atomic_process_containment.py").resolve(
        strict=True
    )
    assert Path(EXACT._CONTAINMENT.__file__).resolve(strict=True) == helper
    assert Path(ORCHESTRATOR._CONTAINMENT.__file__).resolve(strict=True) == helper


def test_nested_exact_gate_controller_is_forced_into_isolated_mode(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    resolved = EXACT._expand_command(
        ("@python@", "tests/run_atomic_release_exact_tag_gate.py", "--help"),
        repo_root=ROOT,
        evidence_root=evidence,
        benchmark_evidence="bench/benchmark.json",
        external_paths={},
        artifact_paths={},
        directory_paths={},
        repository_paths={},
    )
    assert resolved[:3] == (
        str(Path(sys.executable).resolve(strict=True)),
        "-I",
        "tests/run_atomic_release_exact_tag_gate.py",
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX fail-before-target contract")
def test_posix_rejects_supervisor_kill_attack_before_target_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "target-started.txt"
    attack = tmp_path / "kill-supervisor.py"
    attack.write_text(
        "import os, pathlib, signal, subprocess, sys, time\n"
        "pathlib.Path(sys.argv[1]).write_text('target ran', encoding='ascii')\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "os.kill(os.getppid(), signal.SIGKILL)\n"
        "time.sleep(120)\n",
        encoding="ascii",
    )

    spawn_attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def reject_spawn(*args: Any, **kwargs: Any) -> Any:
        spawn_attempts.append((args, kwargs))
        raise AssertionError("POSIX rejection attempted to create a child process")

    monkeypatch.setattr(CONTAINMENT.subprocess, "Popen", reject_spawn)
    with pytest.raises(
        CONTAINMENT.ProcessContainmentError,
        match="unsupported on POSIX; the target was not started",
    ):
        CONTAINMENT.launch_contained(
            [sys.executable, str(attack), str(marker)],
            cwd=tmp_path,
            environment=EXACT._safe_process_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    assert spawn_attempts == []
    assert not marker.exists()


def test_outer_and_inner_output_limits_match_the_release_inventory() -> None:
    policy = json.loads(RELEASE_INVENTORY.read_text(encoding="utf-8"))["releasePolicy"]
    expected = policy["exactTagExternalGates"]["outputLimitBytes"]
    assert expected == 32 * 1024 * 1024
    assert EXACT.MAX_GATE_OUTPUT_BYTES == expected
    assert ORCHESTRATOR.MAX_CHILD_OUTPUT == expected


def test_schema_is_a_real_draft_2020_12_contract() -> None:
    schema = json.loads(SCHEMA_SOURCE.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    encoded = json.dumps(schema, sort_keys=True)
    for gate_id in EXACT.REQUIRED_GATES:
        assert gate_id in encoded
    for name, sha256 in EXACT.FROZEN_EXTERNAL_SHA256.items():
        assert name in encoded
        assert sha256 in encoded
    assert "candidateSamples" in encoded
    assert "searchTimeoutSeconds" in encoded
    assert "pipeline_python" in encoded
    auxiliary = schema["properties"]["auxiliaryRepositories"]
    assert auxiliary["minItems"] == auxiliary["maxItems"] == 3
    assert [
        item["allOf"][1]["properties"]["name"]["const"]
        for item in auxiliary["prefixItems"]
    ] == ["tools", "trainer", "fairy"]


def test_identity_authenticates_exact_clean_tree_and_annotated_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)
    identity = bundle["identity"]
    assert identity["commit"] == git(bundle["repo"], "rev-parse", "HEAD")
    assert identity["tree"] == git(bundle["repo"], "rev-parse", "HEAD^{tree}")
    assert identity["submodules"] == []
    assert identity["tagObject"] != identity["commit"]


@pytest.mark.parametrize("mutation", ["untracked", "tracked", "staged"])
def test_identity_rejects_any_dirty_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)
    repo = bundle["repo"]
    if mutation == "untracked":
        (repo / "untracked.txt").write_text("x", encoding="ascii")
    elif mutation == "tracked":
        (repo / "gate_fixture.py").write_text("changed", encoding="ascii")
    else:
        (repo / "staged.txt").write_text("x", encoding="ascii")
        git(repo, "add", "staged.txt")
    with pytest.raises(EXACT.ExactTagGateError, match="not exact and clean|index tree"):
        identity_for(repo, bundle["identity"]["commit"], bundle["identity"]["tagObject"])


def test_identity_authenticates_initialized_clean_submodules_and_rejects_dirty_child(
    tmp_path: Path,
) -> None:
    child = tmp_path / "child"
    child_commit, child_tree = make_clean_repo(child)
    parent = tmp_path / "parent"
    parent.mkdir()
    git(parent, "init", "--quiet")
    git(parent, "config", "user.email", "atomic-tests@example.invalid")
    git(parent, "config", "user.name", "Atomic tests")
    git(parent, "config", "core.autocrlf", "false")
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(parent),
            "submodule",
            "add",
            "--quiet",
            str(child),
            "deps/child",
        ],
        check=True,
    )
    git(parent, "commit", "--quiet", "-am", "submodule")
    parent_commit = git(parent, "rev-parse", "HEAD")
    tree, submodules = EXACT._validate_repo_tree(parent, parent_commit)
    assert EXACT.SHA1_RE.fullmatch(tree)
    assert submodules == [
        {"path": "deps/child", "commit": child_commit, "tree": child_tree}
    ]
    (parent / "deps/child/dirty.txt").write_text("dirty", encoding="ascii")
    with pytest.raises(EXACT.ExactTagGateError, match="not exact and clean"):
        EXACT._validate_repo_tree(parent, parent_commit)


def test_auxiliary_fairy_repository_is_commit_and_tree_authenticated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)
    records = EXACT.authenticate_aux_repositories(bundle["repositories"])
    assert [record["name"] for record in records] == ["tools", "trainer", "fairy"]
    fairy = bundle["repositories"]["fairy"]
    (fairy / "substituted-fixture.epd").write_text("untracked", encoding="ascii")
    with pytest.raises(EXACT.ExactTagGateError, match="not exact and clean"):
        EXACT.authenticate_aux_repositories(bundle["repositories"])


def test_command_plan_is_tracked_canonical_and_has_no_runtime_command_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)
    plan, record = EXACT.load_command_plan(bundle["repo"], bundle["plan"])
    assert [gate["id"] for gate in plan["gates"]] == list(EXACT.REQUIRED_GATES)
    assert {
        gate["id"]: gate["timeoutSeconds"] for gate in plan["gates"]
    } == EXACT.GATE_TIMEOUT_SECONDS
    assert sum(EXACT.GATE_TIMEOUT_SECONDS.values()) == EXACT.GATE_TIMEOUT_BUDGET_SECONDS
    assert plan["directories"] == ["syzygy_combined"]
    assert "@repository:fairy@" in plan["gates"][0]["argv"]
    assert record["gitBlob"] == git(
        bundle["repo"], "rev-parse", "HEAD:release-plan.json"
    )
    assert record["sha256"] == hashlib.sha256(
        (bundle["repo"] / "release-plan.json").read_bytes()
    ).hexdigest()
    run_help = subprocess.run(
        [sys.executable, str(ROOT / "scripts/atomic_release_exact_tag_gates.py"), "run", "--help"],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    assert "--command-plan" in run_help
    assert "--gate-command" not in run_help


def test_productive_plan_is_canonical_and_matches_every_closed_gate_cli() -> None:
    raw = PRODUCTIVE_PLAN.read_bytes()
    plan = json.loads(raw.decode("utf-8"))
    assert raw == EXACT.canonical_json(plan)
    assert plan["artifacts"] == [
        "candidate_bmi2",
        "native_archive",
        "source_archive",
        "python_wheel",
        "board_wasm_archive",
        "uci_wasm_archive",
        "pyffish",
        "cjs",
        "esm",
        "wasm_wrapper",
        "cpp_unit",
        "cpp_api",
        "syzygy_driver",
        "incremental_binary",
        "pipeline_python",
        "atomic_pipeline_engine",
        "tools_engine",
        "atomic_data_generator",
        "data_tools",
        "wrapper_data_tools",
        "trainer_loader",
        "tools_build_manifest",
        "trainer_build_manifest",
        "atomic_build_manifest",
        "atomic_data_generator_build_manifest",
        "release_manifest",
        "release_checksums",
    ]
    assert plan["directories"] == [
        "atomic_build_root",
        "tools_build_root",
        "trainer_build_root",
        "syzygy_combined",
        "gate_workspace",
    ]
    assert [gate["id"] for gate in plan["gates"]] == list(EXACT.REQUIRED_GATES)
    assert {
        gate["id"]: gate["timeoutSeconds"] for gate in plan["gates"]
    } == EXACT.GATE_TIMEOUT_SECONDS
    for gate in plan["gates"][:-1]:
        arguments = ORCHESTRATOR.parse_args(gate["argv"][2:])
        assert arguments.gate == gate["id"]
        expected_pipeline = gate["id"] in EXACT.PIPELINE_PYTHON_GATES
        assert (EXACT.PIPELINE_PYTHON_PLACEHOLDER in gate["argv"]) == expected_pipeline
    benchmark = plan["gates"][-1]
    assert benchmark["argv"] == [
        "@python@",
        "tests/atomic_bench_compare.py",
        "--candidate",
        "@artifact:candidate_bmi2@",
        "--baseline",
        "@external:fairy_bmi2_baseline@",
        "--eval-file",
        "@external:legacy_net@",
        "--nodes",
        "100000",
        "--hash",
        "64",
        "--affinity",
        "0",
        "--timeout",
        "60",
        "--json-output",
        EXACT.BENCH_EVIDENCE_PLACEHOLDER,
    ]
    assert benchmark["receiptEvidence"] == plan["benchmarkEvidence"]


@pytest.mark.parametrize(
    "argv,marker",
    [
        (["bash", "gate_fixture.py"], "authenticated Python"),
        (["@python@", "-c", "pass"], "tracked .py"),
        (["@python@", "gate_fixture.py", "@artifact:candidate_bmi2@/suffix"], "placeholder"),
        (["@python@", "untracked.py"], "tracked"),
    ],
)
def test_command_plan_rejects_shell_inline_untracked_and_embedded_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    marker: str,
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)
    with pytest.raises(EXACT.ExactTagGateError, match=marker):
        EXACT._validate_command_template(
            bundle["repo"],
            "hito4-release",
            argv,
            ("candidate_bmi2",),
            (),
        )


def test_benchmark_evidence_placeholder_is_exclusive_exact_and_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)
    repo = bundle["repo"]
    evidence = tmp_path / "planned-evidence"
    evidence.mkdir()
    template = [
        "@python@",
        "gate_fixture.py",
        "--json-output",
        EXACT.BENCH_EVIDENCE_PLACEHOLDER,
    ]
    validated = EXACT._validate_command_template(
        repo, EXACT.BENCH_GATE, template, ("candidate_bmi2",), ()
    )
    resolved = EXACT._expand_command(
        validated,
        repo_root=repo,
        evidence_root=evidence,
        benchmark_evidence="bench/benchmark.json",
        external_paths={},
        artifact_paths={},
        directory_paths={},
        repository_paths={},
    )
    assert resolved[-1] == str(evidence.resolve() / "bench/benchmark.json")

    with pytest.raises(EXACT.ExactTagGateError, match="unknown or embedded"):
        EXACT._validate_command_template(
            repo, "hito4-release", template, ("candidate_bmi2",), ()
        )
    with pytest.raises(EXACT.ExactTagGateError, match="exactly one"):
        EXACT._validate_command_template(
            repo,
            EXACT.BENCH_GATE,
            ["@python@", "gate_fixture.py", "--json-output", "benchmark.json"],
            ("candidate_bmi2",),
            (),
        )
    with pytest.raises(EXACT.ExactTagGateError, match="unknown or embedded"):
        EXACT._validate_command_template(
            repo,
            EXACT.BENCH_GATE,
            [
                "@python@",
                "gate_fixture.py",
                "--json-output",
                f"{EXACT.BENCH_EVIDENCE_PLACEHOLDER}/benchmark.json",
            ],
            ("candidate_bmi2",),
            (),
        )


def test_pipeline_python_binding_is_required_only_for_legacy_h5_and_h7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)
    repo = bundle["repo"]
    artifacts = ("candidate_bmi2", "pipeline_python")
    bound = [
        "@python@",
        "gate_fixture.py",
        "--pipeline-python",
        EXACT.PIPELINE_PYTHON_PLACEHOLDER,
    ]
    for gate in sorted(EXACT.PIPELINE_PYTHON_GATES):
        assert EXACT._validate_command_template(repo, gate, bound, artifacts, ())
    with pytest.raises(EXACT.ExactTagGateError, match="exactly 0"):
        EXACT._validate_command_template(repo, "hito4-release", bound, artifacts, ())
    with pytest.raises(EXACT.ExactTagGateError, match="exactly 1"):
        EXACT._validate_command_template(
            repo,
            "legacy-v1-strong-local",
            ["@python@", "gate_fixture.py", "--no-pipeline-python"],
            artifacts,
            (),
        )
    with pytest.raises(EXACT.ExactTagGateError, match="exactly 1"):
        EXACT._validate_command_template(
            repo,
            "hito5-release",
            [*bound, "--duplicate", EXACT.PIPELINE_PYTHON_PLACEHOLDER],
            artifacts,
            (),
        )


def test_gate_environment_is_allowlisted_and_never_forwards_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOP_SECRET_TOKEN", "must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    environment = EXACT._safe_process_environment()
    assert "PATH" in environment
    assert "TOP_SECRET_TOKEN" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "must-not-leak" not in environment.values()


def test_run_writes_schema_valid_canonical_manifest_and_verify_reauthenticates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch)
    value = bundle["value"]
    assert bundle["manifest"].read_bytes() == EXACT.canonical_json(value)
    schema = json.loads((bundle["repo"] / EXACT.SCHEMA_PATH).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)
    assert [gate["id"] for gate in value["gates"]] == list(EXACT.REQUIRED_GATES)
    assert all(gate["status"] == "pass" for gate in value["gates"])
    assert all(gate.get("receipt", {}).get("skipped", 0) == 0 for gate in value["gates"])
    benchmark = value["gates"][-1]["benchmark"]
    assert benchmark["candidateMedianNps"] == "1300000.000000"
    assert benchmark["baselineMedianNps"] == "1040000.000000"
    assert benchmark["ratio"] == "1.250000"
    assert verify_bundle(bundle) == value
    unseal(bundle["evidence"])


@pytest.mark.parametrize(
    "mode,marker",
    [
        ("fail", "exit code 7"),
        ("silent", "missing or unreadable"),
        ("skip", "pass/no-skips"),
        ("dirty-tree", "not exact and clean"),
        ("mutate-artifact", "candidate artifact mismatch"),
        ("mutate-evidence", "changed before manifest"),
    ],
)
def test_run_never_writes_manifest_for_failure_skip_or_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    marker: str,
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, mode=mode, run=False)
    with pytest.raises(EXACT.ExactTagGateError, match=marker):
        run_bundle(bundle)
    assert not bundle["manifest"].exists()
    if bundle["evidence"].exists():
        unseal(bundle["evidence"])


def test_pre_manifest_independent_verification_is_mandatory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch, run=False)

    def reject(*args: Any, **kwargs: Any) -> None:
        raise EXACT.ExactTagGateError("independent verifier rejected")

    monkeypatch.setattr(EXACT, "_verify_sources", reject)
    with pytest.raises(EXACT.ExactTagGateError, match="independent verifier"):
        run_bundle(bundle)
    assert not bundle["manifest"].exists()
    unseal(bundle["evidence"])


@pytest.mark.skipif(os.name != "nt", reason=WINDOWS_CONTAINMENT_REASON)
def test_gate_timeout_and_output_limit_are_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeper = tmp_path / "sleeper.py"
    sleeper.write_text("import time; print('start', flush=True); time.sleep(5)\n", encoding="ascii")
    stdout = tmp_path / "stdout.log"
    stderr = tmp_path / "stderr.log"
    with pytest.raises(EXACT.ExactTagGateError, match="timed out"):
        EXACT._execute_gate(
            [sys.executable, str(sleeper)],
            cwd=tmp_path,
            environment=EXACT._safe_process_environment(),
            timeout_seconds=1,
            stdout_path=stdout,
            stderr_path=stderr,
            gate_id="hito4-release",
        )

    noisy = tmp_path / "noisy.py"
    noisy.write_text("print('x' * 10000, flush=True)\n", encoding="ascii")
    monkeypatch.setattr(EXACT, "MAX_GATE_OUTPUT_BYTES", 100)
    with pytest.raises(EXACT.ExactTagGateError, match="output exceeded"):
        EXACT._execute_gate(
            [sys.executable, str(noisy)],
            cwd=tmp_path,
            environment=EXACT._safe_process_environment(),
            timeout_seconds=10,
            stdout_path=tmp_path / "noisy-stdout.log",
            stderr_path=tmp_path / "noisy-stderr.log",
            gate_id="hito4-release",
        )
    assert (tmp_path / "noisy-stdout.log").stat().st_size <= 100
    assert (tmp_path / "noisy-stderr.log").stat().st_size <= 100
    assert (
        (tmp_path / "noisy-stdout.log").stat().st_size
        + (tmp_path / "noisy-stderr.log").stat().st_size
        <= 100
    )


@pytest.mark.skipif(os.name != "nt", reason=WINDOWS_CONTAINMENT_REASON)
def test_gate_timeout_terminates_a_real_grandchild_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "grandchild.pid"
    parent = tmp_path / "parent.py"
    parent.write_text(
        "\n".join(
            (
                "import pathlib, subprocess, sys, time",
                "child = subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(120)'])",
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='ascii')",
                "print('grandchild ready', flush=True)",
                "time.sleep(120)",
            )
        )
        + "\n",
        encoding="ascii",
    )
    try:
        with pytest.raises(EXACT.ExactTagGateError, match="timed out"):
            EXACT._execute_gate(
                [sys.executable, str(parent), str(pid_file)],
                cwd=tmp_path,
                environment=EXACT._safe_process_environment(),
                timeout_seconds=1,
                stdout_path=tmp_path / "tree-stdout.log",
                stderr_path=tmp_path / "tree-stderr.log",
                gate_id="hito4-release",
            )
        grandchild_pid = int(pid_file.read_text(encoding="ascii"))
        assert wait_process_gone(grandchild_pid), (
            f"grandchild {grandchild_pid} survived the exact-tag timeout"
        )
    finally:
        if pid_file.exists():
            cleanup_process_tree(int(pid_file.read_text(encoding="ascii")))


@pytest.mark.skipif(os.name != "nt", reason=WINDOWS_CONTAINMENT_REASON)
def test_gate_root_exit_still_terminates_inherited_pipe_grandchild(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    stdout = tmp_path / "root-exit-stdout.log"
    stderr = tmp_path / "root-exit-stderr.log"
    parent = tmp_path / "root-exit-parent.py"
    parent.write_text(
        "\n".join(
            (
                "import pathlib, subprocess, sys",
                "child = subprocess.Popen([sys.executable, '-c', "
                "'import time; time.sleep(120)'])",
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='ascii')",
                "print('grandchild inherited stdout', flush=True)",
            )
        )
        + "\n",
        encoding="ascii",
    )
    started = time.monotonic()
    try:
        return_code, _ = EXACT._execute_gate(
            [sys.executable, str(parent), str(pid_file)],
            cwd=tmp_path,
            environment=EXACT._safe_process_environment(),
            timeout_seconds=30,
            stdout_path=stdout,
            stderr_path=stderr,
            gate_id="hito4-release",
        )
        assert return_code == 0
        assert time.monotonic() - started < 10
        assert stdout.read_bytes().replace(b"\r\n", b"\n") == (
            b"grandchild inherited stdout\n"
        )
        assert stderr.read_bytes() == b""
        grandchild_pid = int(pid_file.read_text(encoding="ascii"))
        assert wait_process_gone(grandchild_pid), (
            f"grandchild {grandchild_pid} survived its root process"
        )
    finally:
        if pid_file.exists():
            cleanup_process_tree(int(pid_file.read_text(encoding="ascii")))


@pytest.mark.skipif(os.name != "nt", reason=WINDOWS_CONTAINMENT_REASON)
def test_gate_infinite_writer_is_bounded_and_terminated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "writer.pid"
    writer = tmp_path / "writer.py"
    writer.write_text(
        "\n".join(
            (
                "import os, pathlib, sys",
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()), encoding='ascii')",
                "while True:",
                "    os.write(1, b'x' * 4096)",
            )
        )
        + "\n",
        encoding="ascii",
    )
    monkeypatch.setattr(EXACT, "MAX_GATE_OUTPUT_BYTES", 1024)
    started = time.monotonic()
    try:
        with pytest.raises(EXACT.ExactTagGateError, match="output exceeded"):
            EXACT._execute_gate(
                [sys.executable, str(writer), str(pid_file)],
                cwd=tmp_path,
                environment=EXACT._safe_process_environment(),
                timeout_seconds=30,
                stdout_path=tmp_path / "writer-stdout.log",
                stderr_path=tmp_path / "writer-stderr.log",
                gate_id="hito4-release",
            )
        assert time.monotonic() - started < 10
        writer_pid = int(pid_file.read_text(encoding="ascii"))
        assert wait_process_gone(writer_pid), (
            f"writer {writer_pid} survived the outer output limit"
        )
    finally:
        if pid_file.exists():
            cleanup_process_tree(int(pid_file.read_text(encoding="ascii")))


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object fail-closed path")
def test_windows_job_assignment_failure_never_resumes_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "resumed.txt"
    child = tmp_path / "must-not-resume.py"
    child.write_text(
        "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('bad')\n",
        encoding="ascii",
    )
    child_pids: list[int] = []

    def reject_assignment(job: Any, pid: int) -> None:
        del job
        child_pids.append(pid)
        raise CONTAINMENT.ProcessContainmentError("injected assignment failure")

    monkeypatch.setattr(
        CONTAINMENT, "_assign_process_to_windows_job", reject_assignment
    )
    with pytest.raises(
        CONTAINMENT.ProcessContainmentError, match="injected assignment failure"
    ):
        CONTAINMENT.launch_contained(
            [sys.executable, str(child), str(marker)],
            cwd=tmp_path,
            environment=EXACT._safe_process_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    assert len(child_pids) == 1
    assert wait_process_gone(child_pids[0])
    assert not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows nested Job Object path")
def test_windows_job_containment_supports_a_nested_job(tmp_path: Path) -> None:
    worker = tmp_path / "nested-job-worker.py"
    worker.write_text(
        "\n".join(
            (
                "import os, pathlib, subprocess, sys",
                "root = pathlib.Path(sys.argv[1])",
                "sys.path.insert(0, str(root))",
                "from scripts.atomic_process_containment import launch_contained",
                "nested = launch_contained(",
                "    [sys.executable, '-c', \"print('nested child', flush=True)\"],",
                "    cwd=root, environment=os.environ, stdin=subprocess.DEVNULL,",
                "    stdout=subprocess.PIPE, stderr=subprocess.PIPE,",
                ")",
                "try:",
                "    output, errors = nested.process.communicate(timeout=10)",
                "finally:",
                "    nested.terminate_tree(timeout=10)",
                "assert nested.process.returncode == 0",
                "assert output.replace(b'\\r\\n', b'\\n') == b'nested child\\n'",
                "assert errors == b''",
                "print('nested job passed', flush=True)",
            )
        )
        + "\n",
        encoding="ascii",
    )
    outer = CONTAINMENT.launch_contained(
        [sys.executable, str(worker), str(ROOT)],
        cwd=ROOT,
        environment=EXACT._safe_process_environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        output, errors = outer.process.communicate(timeout=15)
    finally:
        outer.terminate_tree(timeout=10)
    assert outer.process.returncode == 0
    assert output.replace(b"\r\n", b"\n") == b"nested job passed\n"
    assert errors == b""


@pytest.mark.parametrize(
    "path",
    [
        "../escape.log",
        "/absolute.log",
        "nested\\windows.log",
        "network.nnue",
        "table.rtbw",
        "binary.exe",
        "private.pem",
        ".env",
        "credentials.json",
        "archive.tar.xz",
        "a/./b.log",
    ],
)
def test_sensitive_or_escaping_evidence_paths_are_rejected(path: str) -> None:
    with pytest.raises(EXACT.ExactTagGateError):
        EXACT._safe_evidence_relative(path)


def test_reparse_and_special_files_are_rejected_without_platform_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    regular = tmp_path / "regular.log"
    regular.write_text("evidence", encoding="ascii")
    monkeypatch.setattr(EXACT, "_has_reparse_attribute", lambda metadata: True)
    with pytest.raises(EXACT.ExactTagGateError, match="non-reparse"):
        EXACT._regular_file_digest(
            regular, "fixture", maximum=1024
        )

    monkeypatch.setattr(EXACT, "_has_reparse_attribute", lambda metadata: False)
    root = tmp_path / "tree"
    root.mkdir()
    special = root / "special"
    special.write_bytes(b"fixture")
    monkeypatch.setattr(stat, "S_ISREG", lambda mode: False)
    with pytest.raises(EXACT.ExactTagGateError, match="special file"):
        EXACT._scan_evidence_tree(root)


def standalone_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    external = make_external_inputs(tmp_path / "external", monkeypatch)
    external_records = EXACT.authenticate_external_inputs(external)
    candidate = tmp_path / "Atomic-Stockfish-bmi2.exe"
    candidate.write_bytes(b"candidate")
    candidate_record = EXACT._file_record(
        "candidate_bmi2", candidate, "candidate", EXACT.MAX_ARTIFACT_BYTES
    )
    value: dict[str, Any] = {
        "schemaVersion": 1,
        "gate": EXACT.BENCH_GATE,
        "metric": "median-nps",
        "candidate": {
            key: candidate_record[key] for key in ("fileName", "bytes", "sha256")
        },
        "baseline": {
            key: external_records[-1][key] for key in ("fileName", "bytes", "sha256")
        },
        "evalFileSha256": EXACT.FROZEN_EXTERNAL_SHA256["legacy_net"],
        "corpusSha256": EXACT.BENCH_CORPUS_SHA256,
        "positions": 13,
        "nodesPerFen": 100000,
        "threads": 1,
        "hashMb": 64,
        "cpuAffinity": 0,
        "searchTimeoutSeconds": 60,
        "warmups": 1,
        "repetitions": 5,
        "candidateSamples": [
            {"nodes": 1300000, "timeMillis": 1000} for _ in range(5)
        ],
        "baselineSamples": [
            {"nodes": 1300000, "timeMillis": 1250} for _ in range(5)
        ],
        "candidateMedianNps": "1300000.000000",
        "baselineMedianNps": "1040000.000000",
        "ratio": "1.250000",
        "pass": True,
    }
    path = tmp_path / "benchmark.json"
    canonical_write(path, value)
    return path, value, candidate_record, external_records


def test_benchmark_producer_and_exact_tag_verifier_share_one_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = make_external_inputs(tmp_path / "external", monkeypatch)
    external_records = EXACT.authenticate_external_inputs(external)
    candidate = tmp_path / "Atomic-Stockfish-bmi2.exe"
    candidate.write_bytes(b"candidate")
    candidate_record = EXACT._file_record(
        "candidate_bmi2", candidate, "candidate", EXACT.MAX_ARTIFACT_BYTES
    )

    def fingerprint(label: str, path: Path) -> Any:
        payload = path.read_bytes()
        return BENCH.FileFingerprint(label, path, len(payload), digest(payload))

    document = dict(
        BENCH.benchmark_document(
            candidate_fingerprint=fingerprint("candidate", candidate),
            baseline_fingerprint=fingerprint(
                "baseline", external["fairy_bmi2_baseline"]
            ),
            eval_fingerprint=fingerprint("eval_file", external["legacy_net"]),
            candidate_samples=[BENCH.Measurement(1_300_000, 1000) for _ in range(5)],
            baseline_samples=[BENCH.Measurement(1_300_000, 1250) for _ in range(5)],
            affinity=0,
        )
    )
    path = tmp_path / "producer-benchmark.json"
    canonical_write(path, document)
    assert document["searchTimeoutSeconds"] == EXACT.BENCH_SEARCH_TIMEOUT_SECONDS
    assert EXACT.parse_benchmark(
        path,
        candidate_record=candidate_record,
        external_records=external_records,
    ) == document


@pytest.mark.parametrize(
    "mutation,marker",
    [
        (lambda value: value.update({"ratio": "9.999999"}), "derived median/ratio"),
        (lambda value: value.update({"candidateMedianNps": "9999999.000000"}), "derived median/ratio"),
        (lambda value: value.update({"corpusSha256": "0" * 64}), "corpusSha256 mismatch"),
        (lambda value: value.update({"repetitions": 4}), "repetitions mismatch"),
        (lambda value: value.update({"pass": False}), "pass mismatch"),
        (lambda value: value["candidate"].update({"sha256": "0" * 64}), "candidate artifact mismatch"),
        (lambda value: value["candidateSamples"].pop(), "exactly five"),
    ],
)
def test_benchmark_recomputes_samples_and_authenticates_every_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
    marker: str,
) -> None:
    path, value, candidate_record, external_records = standalone_benchmark(
        tmp_path, monkeypatch
    )
    mutation(value)
    canonical_write(path, value)
    with pytest.raises(EXACT.ExactTagGateError, match=marker):
        EXACT.parse_benchmark(
            path,
            candidate_record=candidate_record,
            external_records=external_records,
        )


def test_verify_rejects_noncanonical_manifest_and_tampered_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch)
    bundle["manifest"].chmod(stat.S_IRUSR | stat.S_IWUSR)
    bundle["manifest"].write_text(
        json.dumps(bundle["value"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(EXACT.ExactTagGateError, match="not canonical"):
        verify_bundle(bundle)
    bundle["manifest"].write_bytes(EXACT.canonical_json(bundle["value"]))
    unseal(bundle["evidence"])
    stdout = bundle["evidence"] / "_runner/hito4-release.stdout.log"
    stdout.write_bytes(stdout.read_bytes() + b"tampered\n")
    with pytest.raises(EXACT.ExactTagGateError, match="metadata mismatch"):
        verify_bundle(bundle)


def test_verify_rejects_extra_sensitive_and_hardlinked_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch)
    unseal(bundle["evidence"])
    extra = bundle["evidence"] / "extra.log"
    extra.write_text("not declared", encoding="utf-8")
    with pytest.raises(EXACT.ExactTagGateError, match="file set differs"):
        verify_bundle(bundle)
    extra.unlink()
    leaked = bundle["evidence"] / "leaked.nnue"
    leaked.write_bytes(b"must stay external")
    with pytest.raises(EXACT.ExactTagGateError, match="sensitive evidence extension"):
        verify_bundle(bundle)
    leaked.unlink()
    target = bundle["evidence"] / "hardlink-source.log"
    target.write_text("linked", encoding="ascii")
    linked = bundle["evidence"] / "hardlink.log"
    os.link(target, linked)
    with pytest.raises(EXACT.ExactTagGateError, match="hard-linked"):
        EXACT._scan_evidence_tree(bundle["evidence"])


def test_schema_runtime_rejects_extra_manifest_property(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = make_bundle(tmp_path, monkeypatch)
    schema = json.loads((bundle["repo"] / EXACT.SCHEMA_PATH).read_text(encoding="utf-8"))
    forged = json.loads(json.dumps(bundle["value"]))
    forged["unreviewed"] = True
    with pytest.raises(EXACT.ExactTagGateError, match="unexpected keys"):
        EXACT.validate_manifest_value(forged)
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(forged)
    without_pipeline = json.loads(json.dumps(bundle["value"]))
    without_pipeline["releaseArtifacts"] = [
        item
        for item in without_pipeline["releaseArtifacts"]
        if item["name"] != EXACT.PIPELINE_PYTHON_ARTIFACT
    ]
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(without_pipeline)
    unseal(bundle["evidence"])


def test_manifest_writer_is_exclusive_and_rejects_reparse_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "manifest.json"
    destination.write_bytes(b"keep")
    with pytest.raises(EXACT.ExactTagGateError, match="overwrite"):
        EXACT._write_exclusive(destination, b"replace")
    assert destination.read_bytes() == b"keep"
    destination.unlink()
    monkeypatch.setattr(EXACT, "_has_reparse_attribute", lambda metadata: True)
    with pytest.raises(EXACT.ExactTagGateError, match="non-reparse"):
        EXACT._write_exclusive(destination, b"payload")
