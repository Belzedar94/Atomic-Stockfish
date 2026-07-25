from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.atomic_mining import run_e00_source
from tools.atomic_mining import uci_session


REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = (
    REPO_ROOT
    / "tools"
    / "atomic_mining"
    / "invoke_e00_prepare_smoke.ps1"
)
POWERSHELL = shutil.which("powershell.exe") or shutil.which("powershell")
PRODUCTION_INPUTS = {
    "book": Path(
        r"C:\Users\djime\Documents\Chess_variants\Match script"
        r"\books\atomic.epd"
    ),
    "engine": Path(
        r"C:\Users\djime\Documents\Chess_variants\Codex"
        r"\Fairy-Stockfish organization\Atomic Project"
        r"\Atomic-Stockfish-teacher-syzygy-v2-build-launch1"
        r"\src\atomic-stockfish.exe"
    ),
    "current_net": Path(
        r"D:\NNUE training\Atomic-v2\campaign-28eaed5-high-lambda"
        r"\lambda-100\artifacts\atomic-v3-lambda-100-epoch-37.nnue"
    ),
    "teacher_net": Path(
        r"C:\Users\djime\Documents\Chess_variants\Codex"
        r"\Fairy-Stockfish organization\Atomic Project"
        r"\atomic_run3b_e202_l05.nnue"
    ),
    "variant_config": Path(
        r"C:\Users\djime\Documents\Chess_variants"
        r"\Match script\variants.ini"
    ),
}


def _source() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def _powershell_function(
    source: str, name: str, next_name: str
) -> str:
    return source.split(
        f"function {name}", maxsplit=1
    )[1].split(
        f"function {next_name}", maxsplit=1
    )[0].join((f"function {name}", ""))


def _canonical_sha256(value: object) -> str:
    payload = (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _run_contract_harness(
    tmp_path: Path,
    *,
    mode: str,
    value: object,
) -> subprocess.CompletedProcess[str]:
    source = _source()
    strict_functions = source[
        source.index("function Assert-LowerSha256"):
        source.index("function Assert-PathOutside")
    ]
    file_functions = source[
        source.index("function Get-ByteArraySha256"):
        source.index("function Read-StrictJson")
    ]
    canonical_equality_function = _powershell_function(
        source, "Assert-CanonicalJsonEquals", "Assert-ArtifactMap"
    )
    artifact_function = _powershell_function(
        source, "Assert-ArtifactMap", "Assert-ArtifactEvidence"
    )
    owned_function = _powershell_function(
        source, "Assert-OwnedProcessEvidence", "Assert-RulesCalls"
    )
    rules_function = _powershell_function(
        source, "Assert-RulesCalls", "Assert-ChildImportInventory"
    )
    child_import_function = _powershell_function(
        source, "Assert-ChildImportInventory", "Assert-NativeProvenance"
    )
    harness = tmp_path / f"contract-{mode}.ps1"
    payload = tmp_path / f"contract-{mode}.json"
    payload.write_text(
        json.dumps(value, ensure_ascii=False),
        encoding="utf-8",
    )
    harness.write_text(
        "param([string]$Mode,[string]$Payload)\n"
        "$ErrorActionPreference='Stop'\n"
        "Set-StrictMode -Version Latest\n"
        "$OwnedProcessSchema='atomic-e00-owned-process-v1'\n"
        "$EmptySha256="
        "'e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855'\n"
        f"{strict_functions}\n"
        f"{file_functions}\n"
        f"{canonical_equality_function}\n"
        f"{artifact_function}\n"
        f"{owned_function}\n"
        f"{rules_function}\n"
        f"{child_import_function}\n"
        "$utf8=New-Object System.Text.UTF8Encoding($false,$true)\n"
        "$value=$utf8.GetString([System.IO.File]::ReadAllBytes($Payload)) | "
        "ConvertFrom-Json\n"
        "switch -CaseSensitive ($Mode) {\n"
        "  'boolean' { Assert-JsonBooleanEquals $value $true 'fixture' }\n"
        "  'integer' { Assert-JsonIntegerEquals $value 1 'fixture' }\n"
        "  'number' { Assert-JsonNumberEquals $value 1.5 'fixture' }\n"
        "  'digest' { Get-NamespacedJsonSha256 "
        "$value.namespace $value.value }\n"
        "  'owned' { Assert-OwnedProcessEvidence $value 'fixture' }\n"
        "  'artifact' { Assert-ArtifactMap $value 'fixture' @('one') "
        "-RequireNonEmpty }\n"
        "  'rules' { [void](Assert-RulesCalls $value 'fixture') }\n"
        "  'child-imports' { Assert-ChildImportInventory "
        "$value.actual $value.expected 'fixture' }\n"
        "  default { throw 'unknown harness mode' }\n"
        "}\n"
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
            "-Mode",
            mode,
            "-Payload",
            str(payload),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_windows_powershell_ast_is_clean() -> None:
    assert POWERSHELL is not None
    escaped = str(LAUNCHER).replace("'", "''")
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            (
                f"$path='{escaped}';"
                "$tokens=$null;$errors=$null;"
                "[System.Management.Automation.Language.Parser]::"
                "ParseFile($path,[ref]$tokens,[ref]$errors)|Out-Null;"
                "if($errors.Count){$errors|%{$_.ToString()};exit 1}"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_launcher_is_prepare_and_smoke_only() -> None:
    source = _source()
    assert "$PSScriptRoot" not in source
    assert "$PSCommandPath" in source
    assert "Resolve-RepositoryRoot" in source
    assert "$ExperimentId = 'atomic-e00-src-v3-launch7-20260725'" in source
    assert "$SmokeBatteryId = 'atomic-e00-src-v3-launch7-smoke'" in source
    assert "$FullBatteryId = 'atomic-e00-src-v3-launch7-full'" in source
    assert (
        "$SmokeSeed = 'atomic-e00-src-smoke-v3-launch7-20260725'"
        in source
    )
    assert (
        "$FullSeed = 'atomic-e00-src-full-v3-launch7-20260725'"
        in source
    )
    assert "e00-src-v2" not in source
    assert "launch2" not in source.lower()
    assert "launch3" not in source.lower()
    assert "launch4" not in source.lower()
    assert "launch5" not in source.lower()
    assert "launch6" not in source.lower()
    assert "Invoke-StrictPythonCli '06-smoke' $smokeArguments" in source
    assert source.count("'tools.atomic_mining.run_e00_source'") == 1
    assert (
        "throw \"full output root appeared; full execution is forbidden here\""
        in source
    )


def test_validate_only_returns_before_any_mutation_or_child() -> None:
    source = _source()
    branch = source.index("if ($ValidateOnly) {")
    first_mutation = source.index(
        "# The first mutation occurs only after the complete read-only preflight."
    )
    assert branch < first_mutation
    validate_body = source[branch:first_mutation]
    assert "New-Item" not in validate_body
    assert "Invoke-StrictPythonCli" not in validate_body
    assert "Invoke-CapturedProcessCreateNew" not in validate_body
    assert "Write-NewCompactJson" not in validate_body
    assert "exit 0" in validate_body


def test_no_destructive_or_ambient_process_primitives() -> None:
    source = _source()
    assert "Start-Process" not in source
    assert "Remove-Item" not in source
    assert "cmd.exe" not in source.lower()
    assert "pwsh.exe" not in source.lower()
    assert "New-Object System.Diagnostics.ProcessStartInfo" in source
    assert "$startInfo.Arguments = $quotedArguments -join ' '" in source
    assert "CopyToAsync" in source
    assert "[System.IO.FileMode]::CreateNew" in source


def test_captures_are_outside_the_sealed_design_and_roots_are_fresh() -> None:
    source = _source()
    assert (
        "'F:\\Atomic-V3-E00\\e00-src-v3-launch7-captures'" in source
    )
    assert "Assert-FreshDisjointRoots $targetRoots" in source
    assert (
        "Assert-PathOutside $CaptureRoot $DesignRoot 'capture root'"
        in source
    )
    assert "$stdoutPath = Join-Path $CaptureRoot" in source
    assert "$stderrPath = Join-Path $CaptureRoot" in source
    assert "Test-Path -LiteralPath $leftPath" in source


def test_inputs_and_clean_source_are_frozen() -> None:
    source = _source()
    for digest in (
        "28ed51c2f42e723d5e127d2d3f21c0bfa4a9b318615afdb299b93ea62dea2b1e",
        "86d2bb669ff2a56123a78fd1892c2acf8b4294fb1464da049ddb30877ce5127f",
        "0797cdbaf857aa4552d4eab301227ce3ba7c23a731973c255bb1dfc763659e5b",
        "99dc67eabf26a64faeeca3a88b4c38597a840b8d4a874b9f2cf658c6f92a04a6",
        "30a4779fde75b5259f732a148872aa81dca96da7c766238d0153a591d6624e37",
    ):
        assert digest in source
    assert "status --porcelain=v1 --untracked-files=all" in source
    assert "repository commit/tree differs from the frozen request" in source
    assert "Assert-NoIgnoredPythonBytecode" in source
    assert "__pycache__" in source
    assert ".pyc" in source
    assert "pre-smoke input" in source
    assert "post-smoke input" in source


def test_schedule_and_runtime_contracts_are_exact() -> None:
    source = _source()
    for specification in (
        "VSTC:2000:20:1",
        "VSTC:2000:20:48",
        "STC:10000:100:24",
        "LTC:30000:300:12",
    ):
        assert specification in source
    assert ") 84 168 $fullTimeControls '03-full-schedule'" in source
    assert "'--threads', '1'" in source
    assert "'--maximum-plies', '1024'" in source
    assert "'--command-timeout-seconds', '120'" in source
    assert "'--maximum-game-wall-seconds', '1800'" in source
    assert ") $smokeSchedule 3600 " in source
    assert ") $fullSchedule 14400 " in source


def test_design_is_sealed_before_smoke_and_reverified_after() -> None:
    source = _source()
    publish = source.index("$seal = Publish-DesignSeal")
    first_verify = source.index("Assert-DesignSeal $seal", publish)
    smoke = source.index("Invoke-StrictPythonCli '06-smoke'")
    second_verify = source.index("Assert-DesignSeal $seal", smoke)
    assert publish < first_verify < smoke < second_verify
    assert "$DesignInventorySchema" in source
    assert "$DesignReceiptSchema" in source
    assert "'procedural-independent-hash-bound-v1'" in source
    assert "design.inventory.json" in source
    assert "design.receipt.json" in source


def test_array_producers_do_not_emit_nested_arrays() -> None:
    source = _source()
    for name, next_name in (
        ("Get-DesignInventoryEntries", "Get-DesignDirectoryPaths"),
        ("Get-DesignDirectoryPaths", "Publish-DesignSeal"),
        ("Assert-RulesCalls", "Assert-ChildImportInventory"),
        ("Assert-TimingEvidence", "Assert-RefereeEvidence"),
        ("Get-ExpectedExecutionInputKeys", "Assert-NamespaceGuards"),
    ):
        body = _powershell_function(source, name, next_name)
        assert "return ,$" not in body


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_design_inventory_helpers_emit_flat_sorted_arrays(
    tmp_path: Path,
) -> None:
    source = _source()
    functions = source[
        source.index("function Get-DesignInventoryEntries"):
        source.index("function Publish-DesignSeal")
    ]
    root = tmp_path / "design"
    (root / "zeta" / "nested").mkdir(parents=True)
    (root / "alpha").mkdir()
    (root / "alpha" / "one.bin").write_bytes(b"one")
    (root / "zeta" / "nested" / "two.bin").write_bytes(b"two")
    harness = tmp_path / "flat-design-inventory.ps1"
    harness.write_text(
        "param([string]$Root)\n"
        "$ErrorActionPreference='Stop'\n"
        "Set-StrictMode -Version Latest\n"
        "function Get-StableFileState([string]$Path,[string]$Label){\n"
        "  $item=Get-Item -LiteralPath $Path\n"
        "  return [pscustomobject]@{"
        "Sha256=('0'*64);SizeBytes=[long]$item.Length}\n"
        "}\n"
        f"{functions}\n"
        "$entries=@(Get-DesignInventoryEntries $Root)\n"
        "$directories=@(Get-DesignDirectoryPaths $Root)\n"
        "foreach($entry in $entries){"
        "if($entry -is [System.Array]){throw 'nested entry'}}\n"
        "foreach($directory in $directories){"
        "if($directory -isnot [string]){throw 'nested directory'}}\n"
        "[ordered]@{directories=$directories;"
        "directory_count=$directories.Count;"
        "entries=$entries;file_count=$entries.Count}|"
        "ConvertTo-Json -Compress -Depth 8\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-Root",
            str(root),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["directory_count"] == 3
    assert payload["directories"] == ["alpha", "zeta", "zeta/nested"]
    assert payload["file_count"] == 2
    assert [entry["path"] for entry in payload["entries"]] == [
        "alpha/one.bin",
        "zeta/nested/two.bin",
    ]


def test_smoke_requires_v3_commit_empty_rejections_and_exact_handshake() -> None:
    source = _source()
    assert "$ExecutionReceiptSchema = 'atomic-e00-execution-receipt-v3'" in source
    assert "$GameSchema = 'atomic-e00-game-v3'" in source
    assert "$EngineEvidenceSchema = 'atomic-e00-engine-evidence-v3'" in source
    assert "$RefereeEvidenceSchema = 'atomic-e00-native-referee-evidence-v2'" in source
    assert "$VerifierEvidenceSchema = 'atomic-e00-native-verifier-evidence-v2'" in source
    assert "$OwnedProcessSchema = 'atomic-e00-owned-process-v1'" in source
    assert (
        "Assert-JsonIntegerEquals (\n"
        "        $receipt.reconciliation.accepted_pairs\n"
        "    ) 1 'smoke accepted pairs'"
    ) in source
    assert (
        "Assert-JsonIntegerEquals (\n"
        "        $receipt.reconciliation.accepted_games\n"
        "    ) 2 'smoke accepted games'"
    ) in source
    assert (
        "Assert-JsonIntegerEquals (\n"
        "        $receipt.outputs.rejections.size_bytes\n"
        "    ) 0 'smoke rejection size'"
    ) in source
    assert (
        "Assert-JsonStringEquals (\n"
        "        $receipt.outputs.rejections.sha256\n"
        "    ) $EmptySha256 'smoke rejection SHA-256'"
    ) in source
    assert (
        "Atomic-Stockfish 1.0.3 by the Atomic-Stockfish developers "
        in source
    )
    assert "$HandshakeIdentityOrder = @('name', 'author')" in source
    assert "Assert-JsonBooleanEquals (" in source
    assert "$handshake.observed_blank_after_ids" in source
    assert "Assert-SmokeGame" in source
    assert "Assert-OwnedProcessEvidence" in source
    assert "Assert-RefereeEvidence" in source
    assert "Assert-VerifierEvidence" in source


def test_strict_json_hash_is_from_the_parsed_byte_snapshot() -> None:
    source = _source()
    body = _powershell_function(
        source, "Read-StrictJson", "Read-StrictJsonLines"
    )
    assert "[System.IO.File]::ReadAllBytes($before.FullName)" in body
    assert "Sha256 = Get-ByteArraySha256 $payload" in body
    assert "SizeBytes = [long] $payload.Length" in body
    assert "Get-StableFileState $Path $Label" not in body


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_strict_json_accepts_lf_and_rejects_crlf(
    tmp_path: Path,
) -> None:
    source = _source()
    functions = source[
        source.index("function Get-ByteArraySha256"):
        source.index("function Read-StrictJsonLines")
    ]
    harness = tmp_path / "strict-json.ps1"
    harness.write_text(
        "param([string]$Payload)\n"
        "$ErrorActionPreference='Stop'\n"
        "Set-StrictMode -Version Latest\n"
        f"{functions}\n"
        "[void](Read-StrictJson $Payload 'fixture')\n"
        "Write-Output 'ok'\n",
        encoding="utf-8",
    )
    payload = tmp_path / "payload.json"
    command = [
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
    ]

    payload.write_bytes(b'{"status":"ok"}\n')
    accepted = subprocess.run(
        command, capture_output=True, text=True, timeout=30
    )
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == "ok"

    payload.write_bytes(b'{"status":"ok"}\r\n')
    refused = subprocess.run(
        command, capture_output=True, text=True, timeout=30
    )
    assert refused.returncode != 0
    assert "strict newline-terminated UTF-8 JSON" in refused.stderr


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
@pytest.mark.parametrize(
    ("mode", "valid", "invalid"),
    [
        ("boolean", True, 1),
        ("boolean", True, "true"),
        ("integer", 1, True),
        ("integer", 1, "1"),
        ("number", 1.5, "1.5"),
        ("number", 1.5, True),
    ],
)
def test_json_scalar_contracts_are_type_exact(
    tmp_path: Path,
    mode: str,
    valid: object,
    invalid: object,
) -> None:
    accepted = _run_contract_harness(
        tmp_path, mode=mode, value=valid
    )
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == "ok"
    refused = _run_contract_harness(
        tmp_path, mode=mode, value=invalid
    )
    assert refused.returncode != 0
    assert "must be a JSON" in refused.stderr


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_owned_process_evidence_rejects_bool_coercion_and_omission(
    tmp_path: Path,
) -> None:
    valid = {
        "active_processes_zero": True,
        "containment": "windows-job-object",
        "created_suspended_before_assignment": True,
        "kill_on_close": True,
        "resumed_primary_thread": True,
        "schema": "atomic-e00-owned-process-v1",
        "termination_requested": False,
    }
    accepted = _run_contract_harness(
        tmp_path, mode="owned", value=valid
    )
    assert accepted.returncode == 0, accepted.stderr
    coerced = dict(valid, active_processes_zero=1)
    refused = _run_contract_harness(
        tmp_path, mode="owned", value=coerced
    )
    assert refused.returncode != 0
    assert "must be a JSON boolean" in refused.stderr
    omitted = dict(valid)
    del omitted["termination_requested"]
    refused = _run_contract_harness(
        tmp_path, mode="owned", value=omitted
    )
    assert refused.returncode != 0
    assert "fields differ" in refused.stderr


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_artifact_map_rejects_keyset_and_numeric_string_mutations(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"sealed")
    binding = {
        "identity": {"fixture": "identity"},
        "path": str(artifact),
        "sha256": hashlib.sha256(b"sealed").hexdigest(),
        "size_bytes": len(b"sealed"),
    }
    valid = {"one": binding}
    accepted = _run_contract_harness(
        tmp_path, mode="artifact", value=valid
    )
    assert accepted.returncode == 0, accepted.stderr

    wrong_type = copy.deepcopy(valid)
    wrong_type["one"]["size_bytes"] = str(len(b"sealed"))
    refused = _run_contract_harness(
        tmp_path, mode="artifact", value=wrong_type
    )
    assert refused.returncode != 0
    assert "must be a JSON integer" in refused.stderr

    for mutated in ({}, {"one": binding, "extra": binding}):
        refused = _run_contract_harness(
            tmp_path, mode="artifact", value=mutated
        )
        assert refused.returncode != 0
        assert (
            "fields differ" in refused.stderr
            or "must be non-empty" in refused.stderr
        )


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_child_import_inventory_accepts_empty_python_module_only_as_zero(
    tmp_path: Path,
) -> None:
    empty_digest = hashlib.sha256(b"").hexdigest()
    valid = [
        {
            "module_names": ["urllib"],
            "path": (
                r"C:\Python312\Lib\urllib\__init__.py"
            ),
            "sha256": empty_digest,
            "size_bytes": 0,
            "source": "python-installation",
        }
    ]
    accepted = _run_contract_harness(
        tmp_path,
        mode="child-imports",
        value={"actual": valid, "expected": valid},
    )
    assert accepted.returncode == 0, accepted.stderr

    for bad_size in (-1, "0", False):
        mutated = copy.deepcopy(valid)
        mutated[0]["size_bytes"] = bad_size
        refused = _run_contract_harness(
            tmp_path,
            mode="child-imports",
            value={"actual": mutated, "expected": valid},
        )
        assert refused.returncode != 0
        assert (
            "must be a JSON integer" in refused.stderr
            or "below its minimum" in refused.stderr
        )

    invalid_relations = []
    zero_with_nonempty_digest = copy.deepcopy(valid)
    zero_with_nonempty_digest[0]["sha256"] = hashlib.sha256(
        b"not empty"
    ).hexdigest()
    invalid_relations.append(zero_with_nonempty_digest)
    positive_with_empty_digest = copy.deepcopy(valid)
    positive_with_empty_digest[0]["size_bytes"] = 1
    invalid_relations.append(positive_with_empty_digest)
    for invalid in invalid_relations:
        refused = _run_contract_harness(
            tmp_path,
            mode="child-imports",
            value={"actual": invalid, "expected": invalid},
        )
        assert refused.returncode != 0
        assert "size/SHA-256 relation differs" in refused.stderr


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_rules_calls_reject_digest_drift_and_field_omission(
    tmp_path: Path,
) -> None:
    request = {
        "angle": "<empty>",
        "controls": "\b\f\n\r\t\u0001",
        "literal_escape": r"\u003c",
        "moves": ["e2e4"],
        "quote_and_slash": '"\\/',
        "separators": "\u0085\u2028\u2029<>&'",
        "unicode": "café \U0001f9e8",
    }
    response = {"terminal": True, "count": 1}
    call = {
        "operation": "outcome",
        "request": request,
        "request_sha256": _canonical_sha256(request),
        "response": response,
        "response_sha256": _canonical_sha256(response),
    }
    accepted = _run_contract_harness(
        tmp_path, mode="rules", value=[call]
    )
    assert accepted.returncode == 0, accepted.stderr

    drift = copy.deepcopy(call)
    drift["response_sha256"] = "0" * 64
    refused = _run_contract_harness(
        tmp_path, mode="rules", value=[drift]
    )
    assert refused.returncode != 0
    assert "response digest differs" in refused.stderr

    omitted = copy.deepcopy(call)
    del omitted["request"]
    refused = _run_contract_harness(
        tmp_path, mode="rules", value=[omitted]
    )
    assert refused.returncode != 0
    assert "fields differ" in refused.stderr


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_advertised_option_digest_matches_python_canonical_json(
    tmp_path: Path,
) -> None:
    options = [
        {
            "choices": [],
            "default": "<empty>",
            "kind": "string",
            "maximum": None,
            "minimum": None,
            "name": "Debug Log File",
            "raw": "option name Debug Log File type string default <empty>",
        },
        {
            "choices": ["false", "true", "pure"],
            "default": "true",
            "kind": "combo",
            "maximum": None,
            "minimum": None,
            "name": "Use NNUE",
            "raw": (
                "option name Use NNUE type combo default true "
                "var false var true var pure"
            ),
        },
    ]
    fixture = {
        "namespace": "atomic-e00-advertised-options-v2",
        "value": options,
    }
    completed = _run_contract_harness(
        tmp_path, mode="digest", value=fixture
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        _canonical_sha256(fixture),
        "ok",
    ]


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_real_nineteen_option_digest_matches_recorded_evidence(
    tmp_path: Path,
) -> None:
    declarations = [
        "option name Clear Hash type button",
        "option name Debug Log File type string default <empty>",
        (
            "option name EvalFile type string default "
            "atomic_run3b_e202_l05.nnue"
        ),
        "option name Hash type spin default 16 min 1 max 33554432",
        "option name Move Overhead type spin default 10 min 0 max 5000",
        "option name MultiPV type spin default 1 min 1 max 256",
        "option name nodestime type spin default 0 min 0 max 10000",
        "option name NumaPolicy type string default auto",
        "option name Ponder type check default false",
        "option name Skill Level type spin default 20 min 0 max 20",
        "option name Syzygy50MoveRule type check default true",
        "option name SyzygyPath type string default <empty>",
        "option name SyzygyProbeDepth type spin default 1 min 1 max 100",
        "option name SyzygyProbeLimit type spin default 6 min 0 max 6",
        "option name Threads type spin default 1 min 1 max 1024",
        "option name UCI_Chess960 type check default false",
        (
            "option name UCI_Variant type combo default atomic "
            "var atomic"
        ),
        (
            "option name Use NNUE type combo default true "
            "var false var true var pure"
        ),
        "option name VariantPath type string default <empty>",
    ]
    specs = [
        uci_session._parse_option_declaration(line)
        for line in declarations
    ]
    engine = SimpleNamespace(
        option_specs={spec.name: spec for spec in specs}
    )
    options = run_e00_source._advertised_options(engine)
    fixture = {
        "namespace": "atomic-e00-advertised-options-v2",
        "value": options,
    }
    evidence_digest = (
        "0c2ef87b8de44c286f0f6032ad7f0906"
        "835a731df5038c903959ce189a8e6cf5"
    )
    assert _canonical_sha256(fixture) == evidence_digest

    completed = _run_contract_harness(
        tmp_path, mode="digest", value=fixture
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        evidence_digest,
        "ok",
    ]


@pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is required"
)
def test_process_helper_round_trips_crt_arguments_and_create_new_captures(
    tmp_path: Path,
) -> None:
    source = _source()
    quote_function = _powershell_function(
        source,
        "ConvertTo-WindowsCommandLineArgument",
        "Invoke-CapturedProcessCreateNew",
    )
    process_function = _powershell_function(
        source,
        "Invoke-CapturedProcessCreateNew",
        "Invoke-StrictPythonCli",
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
    arguments_path.write_text(json.dumps(arguments), encoding="utf-8")
    harness = tmp_path / "quote-process-harness.ps1"
    harness.write_text(
        "param([string]$Python,[string]$Helper,[string]$Observed,"
        "[string]$ArgsJson,[string]$WorkingDirectory,[string]$Stdout,"
        "[string]$Stderr)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "Set-StrictMode -Version Latest\n"
        f"{quote_function}\n"
        f"{process_function}\n"
        "$decoded = Get-Content -Raw -LiteralPath $ArgsJson | "
        "ConvertFrom-Json\n"
        "[string[]]$values = $decoded\n"
        "$childArguments = @($Helper, $Observed) + $values\n"
        "$code = Invoke-CapturedProcessCreateNew -FilePath $Python "
        "-ArgumentList $childArguments -WorkingDirectory $WorkingDirectory "
        "-StandardOutputPath $Stdout -StandardErrorPath $Stderr\n"
        "[ordered]@{exit_code=$code} | ConvertTo-Json -Compress\n",
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


def test_no_private_key_or_signature_authority_is_invented() -> None:
    source = _source().lower()
    assert "private key" not in source
    assert "signature" not in source
    assert "hmac" not in source
    assert "procedural-independent-hash-bound-v1" in source


@pytest.mark.skipif(
    POWERSHELL is None
    or not all(path.is_file() for path in PRODUCTION_INPUTS.values()),
    reason="Windows PowerShell and the pinned local inputs are required",
)
@pytest.mark.parametrize("explicit_empty_repository_root", [False, True])
def test_validate_only_rehydrates_in_fresh_process_without_writes(
    tmp_path: Path,
    explicit_empty_repository_root: bool,
) -> None:
    repo = tmp_path / "clean-source"
    shutil.copytree(
        REPO_ROOT / "tools",
        repo / "tools",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "tools"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "fixture"],
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    targets = {
        "design": tmp_path / "fresh-design",
        "smoke": tmp_path / "fresh-smoke",
        "full": tmp_path / "fresh-full",
        "captures": tmp_path / "fresh-captures",
    }
    fixture_launcher = (
        repo
        / "tools"
        / "atomic_mining"
        / "invoke_e00_prepare_smoke.ps1"
    )
    assert fixture_launcher.is_file()
    command = [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(fixture_launcher),
        "-ExpectedSourceCommit",
        commit,
        "-ExpectedSourceTree",
        tree,
    ]
    if explicit_empty_repository_root:
        command.extend(["-RepositoryRoot", ""])
    command.extend(
        [
            "-DesignRoot",
            str(targets["design"]),
            "-SmokeOutputRoot",
            str(targets["smoke"]),
            "-FullOutputRoot",
            str(targets["full"]),
            "-CaptureRoot",
            str(targets["captures"]),
            "-PythonPath",
            sys.executable,
            "-BookPath",
            str(PRODUCTION_INPUTS["book"]),
            "-EnginePath",
            str(PRODUCTION_INPUTS["engine"]),
            "-CurrentNetPath",
            str(PRODUCTION_INPUTS["current_net"]),
            "-TeacherNetPath",
            str(PRODUCTION_INPUTS["teacher_net"]),
            "-VariantConfigPath",
            str(PRODUCTION_INPUTS["variant_config"]),
            "-ValidateOnly",
        ]
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    summary = json.loads(completed.stdout)
    assert summary["status"] == "validated-not-started"
    assert summary["mode"] == "validate-only"
    assert summary["source"]["commit"] == commit
    assert summary["source"]["tree"] == tree
    assert summary["smoke"]["pairs"] == 1
    assert summary["full"]["pairs"] == 84
    assert all(not path.exists() for path in targets.values())
