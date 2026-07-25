from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "tools" / "atomic_mining" / "invoke_e00_full.ps1"
POWERSHELL = shutil.which("powershell.exe")

EXPERIMENT_ID = "atomic-e00-src-v3-launch4-20260725"
SMOKE_BATTERY_ID = "atomic-e00-src-v3-launch4-smoke"
FULL_BATTERY_ID = "atomic-e00-src-v3-launch4-full"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

UCI_OPTIONS = [
    {"name": "UCI_Variant", "value": "atomic"},
    {"name": "Threads", "value": 1},
    {"name": "Hash", "value": 512},
    {"name": "MultiPV", "value": 1},
    {"name": "Ponder", "value": False},
    {"name": "SyzygyPath", "value": ""},
    {"name": "SyzygyProbeLimit", "value": 0},
    {"name": "Use NNUE", "value": "true"},
]

MODULE_NAMES = (
    "tools_init",
    "atomic_mining_init",
    "common",
    "schedule_builder",
    "runner",
    "uci_session",
    "atomic_outcome_helper",
    "binding_builder",
    "owned_process",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _binding(path: Path, *, relative: bool = False) -> dict[str, Any]:
    return {
        "path": path.name if relative else str(path.resolve()),
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
    }


def _git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _execution(maximum_wall_seconds: int) -> dict[str, Any]:
    return {
        "clock_policy": {},
        "command_timeout_seconds": 120,
        "maximum_game_wall_seconds": 1800,
        "maximum_plies": 1024,
        "maximum_wall_seconds": maximum_wall_seconds,
        "threads": 1,
        "uci_options": [dict(row) for row in UCI_OPTIONS],
    }


def _receipt_execution(maximum_wall_seconds: int) -> dict[str, Any]:
    return {
        "clock_policy": {
            "charged_interval": (
                "complete-go-write-flush-to-complete-bestmove-newline"
            ),
            "equality": "elapsed-ns-equal-remaining-ns-is-on-time",
            "uci_millisecond_conversion": "floor-nanoseconds",
        },
        "command_timeout_seconds": 120,
        "maximum_game_wall_seconds": 1800,
        "maximum_plies": 1024,
        "maximum_wall_seconds": maximum_wall_seconds,
        "network_options": {
            "current-v3": [
                {"name": "EvalFile", "value": "current.nnue"}
            ],
            "run3b": [
                {"name": "EvalFile", "value": "teacher.nnue"}
            ],
        },
        "owned_process_policy": (
            "windows-job-create-suspended-assign-resume-active-zero-v1"
        ),
        "pair_order": "schedule-ordinal-then-leg",
        "referee_policy": "fresh-exact-pyffish-root-plus-history-v2",
        "retry_policy": "none",
        "threads": 1,
        "time_loss_rule": (
            "draw-if-nonflagging-side-has-insufficient-atomic-"
            "mating-material-v1"
        ),
        "uci_options": [dict(row) for row in UCI_OPTIONS],
        "verifier_policy": (
            "independent-fresh-pyffish-owned-process-replay-v2"
        ),
    }


def _run_receipt_execution_harness(
    tmp_path: Path,
    execution: dict[str, Any],
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    source = LAUNCHER.read_text(encoding="utf-8")
    exact_function = _powershell_function(
        source, "Assert-ExactProperties", "Assert-JsonString"
    )
    strict_functions = source[
        source.index("function Assert-JsonString"):
        source.index("function Get-StableFileState")
    ]
    contract_function = _powershell_function(
        source,
        "Assert-ReceiptExecutionContract",
        "Assert-ExecutionReceipt",
    )
    payload = tmp_path / "receipt-execution.json"
    _write_json(
        payload,
        {
            "execution": execution,
            "receipt": {
                "input_snapshots": {
                    "current_net": {"path": "current.nnue"},
                    "teacher_net": {"path": "teacher.nnue"},
                }
            },
        },
    )
    harness = tmp_path / "receipt-execution.ps1"
    harness.write_text(
        "param([string]$Payload)\n"
        "$ErrorActionPreference='Stop'\n"
        "Set-StrictMode -Version Latest\n"
        f"{exact_function}\n"
        f"{strict_functions}\n"
        f"{contract_function}\n"
        "$utf8=New-Object System.Text.UTF8Encoding($false,$true)\n"
        "$value=$utf8.GetString([System.IO.File]::ReadAllBytes($Payload)) | "
        "ConvertFrom-Json\n"
        "Assert-ReceiptExecutionContract "
        "$value.execution $value.receipt 14400.0 'fixture'\n"
        "Write-Output 'ok'\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-Payload",
            str(payload),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _publish_schedule(
    root: Path,
    *,
    pairs: int,
    games: int,
    seed: str,
) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    schedule = root / "schedule.jsonl"
    schedule.write_bytes(b"{}\n" * games)
    receipt = root / "schedule.receipt.json"
    _write_json(
        receipt,
        {
            "book": {},
            "counts": {"games": games, "pairs": pairs},
            "derivation": {},
            "schedule": {
                "row_count": games,
                "sha256": _sha(schedule),
                "size_bytes": schedule.stat().st_size,
            },
            "schedule_schema": "atomic-e00-schedule-v1",
            "schema": "atomic-e00-schedule-receipt-v1",
            "seed": seed,
            "selected_root_identities": [],
            "time_controls": [],
        },
    )
    return schedule, receipt


def _create_source_repo(root: Path) -> tuple[dict[str, Path], str, str]:
    repo = root / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "E00 fixture")

    paths = {
        "tools_init": repo / "tools" / "__init__.py",
        "atomic_mining_init": (
            repo / "tools" / "atomic_mining" / "__init__.py"
        ),
        "common": repo / "tools" / "atomic_mining" / "common.py",
        "schedule_builder": (
            repo / "tools" / "atomic_mining" / "build_e00_schedule.py"
        ),
        "runner": repo / "tools" / "atomic_mining" / "run_e00_source.py",
        "uci_session": (
            repo / "tools" / "atomic_mining" / "uci_session.py"
        ),
        "atomic_outcome_helper": (
            repo
            / "tools"
            / "atomic_mining"
            / "atomic_outcome_helper.py"
        ),
        "binding_builder": (
            repo
            / "tools"
            / "atomic_mining"
            / "build_atomic_outcome_binding.py"
        ),
        "owned_process": (
            repo / "tools" / "atomic_mining" / "owned_process.py"
        ),
    }
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# sealed fixture: {name}\n", encoding="utf-8")
    shutil.copy2(
        LAUNCHER,
        repo / "tools" / "atomic_mining" / "invoke_e00_full.ps1",
    )
    (repo / ".gitignore").write_text(
        "__pycache__/\n*.pyc\n*.pyo\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "sealed fixture")
    return (
        paths,
        _git(repo, "rev-parse", "HEAD").lower(),
        _git(repo, "rev-parse", "HEAD^{tree}").lower(),
    )


def _authorize(fixture: dict[str, Any]) -> None:
    audit = {
        "audit": {"p0": 0, "p1": 0, "verdict": "GO"},
        "experiment_id": EXPERIMENT_ID,
        "full": {
            "battery_id": FULL_BATTERY_ID,
            "runtime_build_receipt_sha256": _sha(
                fixture["runtime_build_receipt"]
            ),
            "schedule_receipt_sha256": _sha(
                fixture["full_schedule_receipt"]
            ),
        },
        "schema": "atomic-e00-full-authorization-v1",
        "smoke": {
            "battery_id": SMOKE_BATTERY_ID,
            "execution_receipt_sha256": _sha(fixture["smoke_receipt"]),
        },
        "source": {
            "commit": fixture["commit"],
            "tree": fixture["tree"],
        },
    }
    _write_json(fixture["audit"], audit)


def _fixture(tmp_path: Path) -> dict[str, Any]:
    module_paths, commit, tree = _create_source_repo(tmp_path)
    source_root = next(iter(module_paths.values())).parents[1]

    design = tmp_path / "e00-src-v3-launch4-design"
    smoke_root = tmp_path / "e00-src-v3-launch4-smoke"
    full_root = tmp_path / "e00-src-v3-launch4-full"
    capture_root = tmp_path / "e00-src-v3-launch4-captures"
    smoke_root.mkdir()
    capture_root.mkdir()

    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir()
    book = inputs_root / "atomic.epd"
    engine = inputs_root / "engine.exe"
    current_net = inputs_root / "current.nnue"
    teacher_net = inputs_root / "teacher.nnue"
    variant_config = inputs_root / "variants.ini"
    for path in (book, engine, current_net, teacher_net, variant_config):
        path.write_bytes(f"sealed-{path.name}\n".encode("ascii"))

    binding_root = design / "native-binding" / "binding"
    binding_root.mkdir(parents=True)
    pyffish = binding_root / "pyffish.pyd"
    pyffish.write_bytes(b"sealed-pyffish-fixture\n")
    pyffish_manifest = design / "native-binding" / "manifest.json"
    _write_json(pyffish_manifest, {"schema": "fixture"})

    full_schedule, full_schedule_receipt = _publish_schedule(
        design / "full-schedule",
        pairs=84,
        games=168,
        seed="atomic-e00-src-full-v3-launch4-20260725",
    )
    smoke_schedule, smoke_schedule_receipt = _publish_schedule(
        design / "smoke-schedule",
        pairs=1,
        games=2,
        seed="atomic-e00-src-smoke-v3-launch4-20260725",
    )

    input_paths = {
        **module_paths,
        "book": book,
        "engine": engine,
        "current_net": current_net,
        "teacher_net": teacher_net,
        "variant_config": variant_config,
        "pyffish": pyffish,
        "pyffish_build_manifest": pyffish_manifest,
        "schedule": full_schedule,
        "schedule_receipt": full_schedule_receipt,
    }
    assert set(input_paths) == {
        *MODULE_NAMES,
        "book",
        "engine",
        "current_net",
        "teacher_net",
        "variant_config",
        "pyffish",
        "pyffish_build_manifest",
        "schedule",
        "schedule_receipt",
    }
    input_hashes = {name: _sha(path) for name, path in input_paths.items()}

    python = inputs_root / "python.exe"
    python.write_bytes(b"not-an-executable-validation-sentinel\n")
    runtime_manifest = design / "full-runtime" / "runtime-manifest.json"
    _write_json(
        runtime_manifest,
        {
            "execution": _execution(14400),
            "inputs": input_hashes,
            "runtime": {
                "modules": {},
                "native_rules": {},
                "python": {
                    "executable": str(python),
                    "executable_sha256": _sha(python),
                    "executable_size_bytes": python.stat().st_size,
                },
                "source": {
                    "clean": True,
                    "commit": commit,
                    "root": str(source_root.resolve()),
                    "tree": tree,
                },
            },
            "schema": "atomic-e00-runtime-manifest-v2",
        },
    )
    runtime_discovery = design / "full-runtime" / "runtime-discovery.json"
    _write_json(runtime_discovery, {"schema": "fixture"})
    runtime_build_receipt = design / "full-runtime" / "build.receipt.json"
    _write_json(
        runtime_build_receipt,
        {
            "child_import_inventory": [],
            "execution": _execution(14400),
            "inputs": input_hashes,
            "runtime_discovery": _binding(runtime_discovery),
            "runtime_manifest": _binding(runtime_manifest),
            "schema": "atomic-e00-runtime-manifest-build-receipt-v1",
            "source": {
                "commit": commit,
                "root": str(source_root.resolve()),
                "tree": tree,
            },
        },
    )

    games = smoke_root / "games.jsonl"
    inventory = smoke_root / "inventory.json"
    rejections = smoke_root / "rejections.jsonl"
    games.write_bytes(b"{}\n{}\n")
    inventory.write_bytes(b"{}\n")
    rejections.write_bytes(b"")
    smoke_receipt = smoke_root / "receipt.json"
    _write_json(
        smoke_receipt,
        {
            "battery_id": SMOKE_BATTERY_ID,
            "executed_runtime_package": {},
            "execution": {},
            "experiment_id": EXPERIMENT_ID,
            "input_snapshots": {},
            "inputs": {},
            "namespace_guards": {},
            "native_build_artifacts": {},
            "outputs": {
                "games": _binding(games, relative=True),
                "inventory": _binding(inventory, relative=True),
                "rejections": _binding(rejections, relative=True),
            },
            "reconciliation": {
                "accepted_games": 2,
                "accepted_pairs": 1,
                "all_ids_recomputed": True,
                "all_pairs_atomic": True,
                "all_trajectories_legally_replayed": True,
                "draws": 0,
                "losses_current": 1,
                "time_losses": 0,
                "wins_current": 1,
                "zero_extra_duplicate_partial_rows": True,
            },
            "runtime": {},
            "schedule": {
                "receipt_sha256": _sha(smoke_schedule_receipt),
                "receipt_size_bytes": smoke_schedule_receipt.stat().st_size,
                "sha256": _sha(smoke_schedule),
                "size_bytes": smoke_schedule.stat().st_size,
            },
            "schema": "atomic-e00-execution-receipt-v3",
            "status": "committed",
            "trust_boundary": {},
        },
    )

    fixture: dict[str, Any] = {
        "audit": tmp_path / "authorization.json",
        "book": book,
        "capture_root": capture_root,
        "commit": commit,
        "current_net": current_net,
        "design": design,
        "engine": engine,
        "full_root": full_root,
        "full_schedule": full_schedule,
        "full_schedule_receipt": full_schedule_receipt,
        "launcher": (
            source_root
            / "tools"
            / "atomic_mining"
            / "invoke_e00_full.ps1"
        ),
        "python": python,
        "repo": source_root,
        "runtime_build_receipt": runtime_build_receipt,
        "smoke_receipt": smoke_receipt,
        "smoke_rejections": rejections,
        "smoke_root": smoke_root,
        "teacher_net": teacher_net,
        "tree": tree,
        "variant_config": variant_config,
    }
    _authorize(fixture)
    return fixture


def _invoke(
    fixture: dict[str, Any],
    *,
    derive_repository_root: bool = False,
    explicit_empty_repository_root: bool = False,
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    command = [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(fixture["launcher"] if derive_repository_root else LAUNCHER),
        "-ValidateOnly",
        "-AuditReceiptPath",
        str(fixture["audit"]),
    ]
    if not derive_repository_root:
        command.extend(["-RepositoryRoot", str(fixture["repo"])])
    elif explicit_empty_repository_root:
        command.extend(["-RepositoryRoot", ""])
    command.extend(
        [
            "-DesignRoot",
            str(fixture["design"]),
            "-SmokeOutputRoot",
            str(fixture["smoke_root"]),
            "-FullOutputRoot",
            str(fixture["full_root"]),
            "-CaptureRoot",
            str(fixture["capture_root"]),
            "-PythonPath",
            str(fixture["python"]),
            "-BookPath",
            str(fixture["book"]),
            "-EnginePath",
            str(fixture["engine"]),
            "-CurrentNetPath",
            str(fixture["current_net"]),
            "-TeacherNetPath",
            str(fixture["teacher_net"]),
            "-VariantConfigPath",
            str(fixture["variant_config"]),
        ]
    )
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _powershell_function(
    source: str, name: str, next_name: str
) -> str:
    return source.split(
        f"function {name}", maxsplit=1
    )[1].split(
        f"function {next_name}", maxsplit=1
    )[0].join((f"function {name}", ""))


pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)


def test_strict_json_hash_is_of_the_exact_parsed_byte_snapshot() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    body = source.split(
        "function Read-StrictJson", maxsplit=1
    )[1].split(
        "function Read-CanonicalAuditReceipt", maxsplit=1
    )[0]
    assert "[System.IO.File]::ReadAllBytes" in body
    assert "Sha256 = Get-ByteArraySha256 $payload" in body
    assert "SizeBytes = [long] $payload.Length" in body
    assert "Get-StableFileState $Path $Label" not in body


def test_json_contracts_use_type_exact_validators() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    for helper in (
        "Assert-JsonStringEquals",
        "Assert-JsonBooleanEquals",
        "Assert-JsonIntegerEquals",
        "Assert-JsonNumberEquals",
        "Assert-JsonScalarEquals",
    ):
        assert f"function {helper}" in source
    for coercive in (
        "[long] $value.audit.p0",
        "[long] $value.audit.p1",
        "[long] $receipt.counts.pairs",
        "[long] $receipt.counts.games",
        "[long] $receipt.reconciliation.accepted_pairs",
        "[long] $receipt.reconciliation.accepted_games",
        "$receipt.reconciliation.all_pairs_atomic -ne $true",
        "$manifest.runtime.source.clean -ne $true",
    ):
        assert coercive not in source


def test_full_result_receipt_execution_contract_is_type_exact(
    tmp_path: Path,
) -> None:
    valid = _run_receipt_execution_harness(
        tmp_path, _receipt_execution(14400)
    )
    assert valid.returncode == 0, valid.stderr
    assert valid.stdout.strip() == "ok"

    for section, field, value, error in (
        (None, "threads", "1", "must be a JSON integer"),
        (
            None,
            "command_timeout_seconds",
            "120",
            "must be a JSON number",
        ),
        ("uci", "Ponder", 0, "must be a JSON boolean"),
    ):
        execution = _receipt_execution(14400)
        if section == "uci":
            option = next(
                row
                for row in execution["uci_options"]
                if row["name"] == field
            )
            option["value"] = value
        else:
            execution[field] = value
        refused = _run_receipt_execution_harness(
            tmp_path, execution
        )
        assert refused.returncode != 0
        assert error in refused.stderr


def test_full_result_requires_deep_receipt_reauthentication() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    expected_keys = _powershell_function(
        source, "Get-ExpectedExecutionInputKeys", "Assert-NamespaceGuards"
    )
    assert "return ,$" not in expected_keys
    assert "'full' -RequireComplete" in source
    assert "Get-ExpectedExecutionInputKeys $receipt" in source
    assert (
        "'runtime_manifest', 'python_executable'"
        in source
    )
    assert "$runtimePackageKeys = @(" in source
    assert "Assert-NamespaceGuards (" in source
    assert "$receipt.trust_boundary.hermetic" in source
    assert "Assert-ReceiptExecutionContract (" in source
    assert (
        "The smoke evidence was deeply validated by the prepare launcher"
        in source
    )


def test_process_helper_round_trips_windows_crt_arguments_and_raw_captures(
    tmp_path: Path,
) -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    quote_function = _powershell_function(
        source,
        "ConvertTo-WindowsCommandLineArgument",
        "Invoke-CapturedProcessCreateNew",
    )
    process_function = _powershell_function(
        source,
        "Invoke-CapturedProcessCreateNew",
        "Assert-FileBinding",
    )

    helper = tmp_path / "helper with spaces.py"
    observed = tmp_path / "observed arguments.json"
    stdout = tmp_path / "raw stdout.bin"
    stderr = tmp_path / "raw stderr.bin"
    arguments_path = tmp_path / "arguments.json"
    helper.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text("
        "json.dumps(sys.argv[2:]), encoding='utf-8')\n"
        "sys.stdout.write('stdout-sentinel\\n')\n"
        "sys.stderr.write('stderr-sentinel\\n')\n",
        encoding="utf-8",
    )
    arguments = [
        "plain",
        "with spaces",
        "",
        'embedded"quote',
        "trailing space\\",
        r"two\\slashes",
        'mix \\\\"quote\\end',
        "--looks-like-option",
        "caf\u00e9",
    ]
    arguments_path.write_text(
        json.dumps(arguments), encoding="utf-8"
    )
    harness = tmp_path / "quote-process-harness.ps1"
    harness.write_text(
        "param([string]$Python,[string]$Helper,[string]$Observed,"
        "[string]$ArgsJson,[string]$WorkingDirectory,"
        "[string]$Stdout,[string]$Stderr)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "Set-StrictMode -Version Latest\n"
        f"{quote_function}\n"
        f"{process_function}\n"
        "$decoded = Get-Content -Raw -LiteralPath $ArgsJson | "
        "ConvertFrom-Json\n"
        "[string[]]$values = $decoded\n"
        "$childArguments = @($Helper, $Observed) + $values\n"
        "$code = Invoke-CapturedProcessCreateNew "
        "-FilePath $Python -ArgumentList $childArguments "
        "-WorkingDirectory $WorkingDirectory "
        "-StandardOutputPath $Stdout -StandardErrorPath $Stderr\n"
        "[ordered]@{exit_code=$code} | "
        "ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    command = [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(harness),
        "-Python",
        sys.executable,
        "-Helper",
        str(helper),
        "-Observed",
        str(observed),
        "-ArgsJson",
        str(arguments_path),
        "-WorkingDirectory",
        str(tmp_path),
        "-Stdout",
        str(stdout),
        "-Stderr",
        str(stderr),
    ]

    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=30
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"exit_code": 0}
    assert json.loads(observed.read_text(encoding="utf-8")) == arguments
    assert stdout.read_bytes() in (
        b"stdout-sentinel\n",
        b"stdout-sentinel\r\n",
    )
    assert stderr.read_bytes() in (
        b"stderr-sentinel\n",
        b"stderr-sentinel\r\n",
    )

    observed.unlink()
    refused = subprocess.run(
        command, capture_output=True, text=True, timeout=30
    )
    assert refused.returncode != 0
    assert not observed.exists()
    assert stdout.exists()
    assert stderr.exists()


def test_launcher_has_no_start_process_or_design_root_captures() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert EXPERIMENT_ID in source
    assert SMOKE_BATTERY_ID in source
    assert FULL_BATTERY_ID in source
    assert "launch3" not in source.lower()
    assert "Start-Process" not in source
    assert "$PSScriptRoot" not in source
    assert "$PSCommandPath" in source
    assert "Resolve-RepositoryRoot" in source
    assert "[System.IO.FileMode]::CreateNew" in source
    assert "$FullStdoutPath = Join-Path $CaptureRoot" in source
    assert "$FullStderrPath = Join-Path $CaptureRoot" in source


def test_validate_only_is_fresh_process_rehydratable_and_non_mutating(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    completed = _invoke(fixture)

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    value = json.loads(completed.stdout)
    assert value["status"] == "validated-not-started"
    assert value["experiment_id"] == EXPERIMENT_ID
    assert value["battery_id"] == FULL_BATTERY_ID
    assert value["source"] == {
        "commit": fixture["commit"],
        "tree": fixture["tree"],
    }
    assert value["argv"][:3] == [
        "-B",
        "-m",
        "tools.atomic_mining.run_e00_source",
    ]
    assert str(fixture["full_root"]) in value["argv"]
    assert not fixture["full_root"].exists()
    assert not Path(value["stdout_path"]).exists()
    assert not Path(value["stderr_path"]).exists()


@pytest.mark.parametrize("explicit_empty_repository_root", [False, True])
def test_validate_only_derives_repository_from_pscommandpath_without_writes(
    tmp_path: Path,
    explicit_empty_repository_root: bool,
) -> None:
    fixture = _fixture(tmp_path)

    completed = _invoke(
        fixture,
        derive_repository_root=True,
        explicit_empty_repository_root=explicit_empty_repository_root,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    value = json.loads(completed.stdout)
    assert value["status"] == "validated-not-started"
    assert value["source"] == {
        "commit": fixture["commit"],
        "tree": fixture["tree"],
    }
    assert not fixture["full_root"].exists()
    assert not Path(value["stdout_path"]).exists()
    assert not Path(value["stderr_path"]).exists()


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("schema", "atomic-e00-execution-receipt-v2", "execution"),
        ("status", "prepared", "committed"),
    ],
)
def test_rejects_reauthorized_non_v3_or_uncommitted_smoke(
    tmp_path: Path,
    field: str,
    value: str,
    error: str,
) -> None:
    fixture = _fixture(tmp_path)
    receipt = _read_json(fixture["smoke_receipt"])
    receipt[field] = value
    _write_json(fixture["smoke_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert error.lower() in completed.stderr.lower()


def test_rejects_reauthorized_smoke_output_name_permutation(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    receipt = _read_json(fixture["smoke_receipt"])
    receipt["outputs"]["games"]["path"] = "inventory.json"
    receipt["outputs"]["inventory"]["path"] = "games.jsonl"
    _write_json(fixture["smoke_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "output games path differs" in completed.stderr


def test_rejects_reauthorized_nonempty_smoke_rejection_ledger(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    fixture["smoke_rejections"].write_bytes(b'{"rejected":true}\n')
    receipt = _read_json(fixture["smoke_receipt"])
    receipt["outputs"]["rejections"] = _binding(
        fixture["smoke_rejections"], relative=True
    )
    _write_json(fixture["smoke_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "rejection ledger is not byte-empty" in completed.stderr


def test_rejects_non_go_authorization_even_when_canonical_json(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    audit = _read_json(fixture["audit"])
    audit["audit"]["p1"] = 1
    _write_json(fixture["audit"], audit)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "not GO with P0=0 and P1=0" in completed.stderr


@pytest.mark.parametrize("value", ["0", False])
def test_rejects_type_coerced_authorization_counts(
    tmp_path: Path,
    value: object,
) -> None:
    fixture = _fixture(tmp_path)
    audit = _read_json(fixture["audit"])
    audit["audit"]["p0"] = value
    _write_json(fixture["audit"], audit)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "must be a JSON integer" in completed.stderr


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("pairs", "84", "must be a JSON integer"),
        ("games", True, "must be a JSON integer"),
    ],
)
def test_rejects_type_coerced_schedule_counts(
    tmp_path: Path,
    field: str,
    value: object,
    error: str,
) -> None:
    fixture = _fixture(tmp_path)
    receipt = _read_json(fixture["full_schedule_receipt"])
    receipt["counts"][field] = value
    _write_json(fixture["full_schedule_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert error in completed.stderr


def test_rejects_omitted_schedule_count(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt = _read_json(fixture["full_schedule_receipt"])
    del receipt["counts"]["games"]
    _write_json(fixture["full_schedule_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "counts fields differ" in completed.stderr


@pytest.mark.parametrize("value", [1, "true"])
def test_rejects_type_coerced_smoke_reconciliation_boolean(
    tmp_path: Path,
    value: object,
) -> None:
    fixture = _fixture(tmp_path)
    receipt = _read_json(fixture["smoke_receipt"])
    receipt["reconciliation"]["all_pairs_atomic"] = value
    _write_json(fixture["smoke_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "must be a JSON boolean" in completed.stderr


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("threads", "1", "must be a JSON integer"),
        ("maximum_plies", True, "must be a JSON integer"),
        ("command_timeout_seconds", "120", "must be a JSON number"),
    ],
)
def test_rejects_type_coerced_runtime_execution_scalars(
    tmp_path: Path,
    field: str,
    value: object,
    error: str,
) -> None:
    fixture = _fixture(tmp_path)
    receipt = _read_json(fixture["runtime_build_receipt"])
    receipt["execution"][field] = value
    _write_json(fixture["runtime_build_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert error in completed.stderr


def test_rejects_integer_instead_of_uci_boolean(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt = _read_json(fixture["runtime_build_receipt"])
    receipt["execution"]["uci_options"][4]["value"] = 0
    _write_json(fixture["runtime_build_receipt"], receipt)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "must be a JSON boolean" in completed.stderr


def test_rejects_integer_instead_of_manifest_clean_boolean(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    manifest_path = fixture["runtime_build_receipt"].parent / (
        "runtime-manifest.json"
    )
    manifest = _read_json(manifest_path)
    manifest["runtime"]["source"]["clean"] = 1
    _write_json(manifest_path, manifest)
    build = _read_json(fixture["runtime_build_receipt"])
    build["runtime_manifest"] = _binding(manifest_path)
    _write_json(fixture["runtime_build_receipt"], build)
    _authorize(fixture)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "must be a JSON boolean" in completed.stderr


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("full", "runtime_build_receipt_sha256"),
        ("full", "schedule_receipt_sha256"),
        ("smoke", "execution_receipt_sha256"),
    ],
)
def test_rejects_wrong_authorized_hash(
    tmp_path: Path,
    section: str,
    field: str,
) -> None:
    fixture = _fixture(tmp_path)
    audit = _read_json(fixture["audit"])
    audit[section][field] = "0" * 64
    _write_json(fixture["audit"], audit)

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "differs" in completed.stderr


def test_rejects_schedule_drift_after_authorization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture["full_schedule"].write_bytes(
        fixture["full_schedule"].read_bytes() + b"{}\n"
    )

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "full schedule schedule binding differs" in completed.stderr


def test_rejects_dirty_source_after_authorization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture["repo"] / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "repository is not exactly clean" in completed.stderr


@pytest.mark.parametrize("kind", ["cache", "pyc"])
def test_rejects_ignored_python_bytecode(
    tmp_path: Path,
    kind: str,
) -> None:
    fixture = _fixture(tmp_path)
    package = fixture["repo"] / "tools" / "atomic_mining"
    if kind == "cache":
        cache = package / "__pycache__"
        cache.mkdir()
        (cache / "runner.cpython-312.pyc").write_bytes(b"stale")
    else:
        (package / "runner.pyc").write_bytes(b"stale")

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "ignored Python bytecode" in completed.stderr


@pytest.mark.parametrize("target", ["root", "stdout", "stderr"])
def test_rejects_any_preexisting_full_target(
    tmp_path: Path,
    target: str,
) -> None:
    fixture = _fixture(tmp_path)
    if target == "root":
        fixture["full_root"].mkdir()
    else:
        suffix = "stdout.json" if target == "stdout" else "stderr.bin"
        (
            fixture["capture_root"] / f"{FULL_BATTERY_ID}.{suffix}"
        ).write_bytes(b"preexisting")

    completed = _invoke(fixture)

    assert completed.returncode != 0
    assert "already exists" in completed.stderr
