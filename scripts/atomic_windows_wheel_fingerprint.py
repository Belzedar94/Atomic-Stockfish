#!/usr/bin/env python3
"""Create a fail-closed fingerprint of the Windows Python wheel toolchain.

The output is intended to be embedded in Atomic-Stockfish release provenance.
It deliberately records the concrete GitHub runner image, Visual Studio/SDK,
CPython executable, packaging tools, and MSVC binaries used for a wheel build.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as importlib_metadata
import json
import locale
import ntpath
import os
import platform
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, NamedTuple, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PACKAGE_NAME_RE = re.compile(r"[-_.]+")
_VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class FingerprintError(RuntimeError):
    """Raised when the build environment cannot be identified unambiguously."""


class CommandResult(NamedTuple):
    returncode: int
    stdout: bytes
    stderr: bytes


CommandRunner = Callable[[Sequence[str], Optional[Mapping[str, str]]], CommandResult]


def run_command(
    argv: Sequence[str], environment: Optional[Mapping[str, str]] = None
) -> CommandResult:
    """Run a command without shell interpretation and retain its exact bytes."""

    try:
        completed = subprocess.run(
            list(argv),
            env=None if environment is None else dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise FingerprintError("failed to execute {!r}: {}".format(list(argv), exc)) from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _is_windows() -> bool:
    return os.name == "nt" and sys.platform == "win32"


def _clean_scalar(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise FingerprintError("{} must be a string".format(label))
    if not value or value != value.strip():
        raise FingerprintError("{} must be non-empty and have no outer whitespace".format(label))
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise FingerprintError("{} contains a control character".format(label))
    return value


def _index_environment(environment: Mapping[str, str]) -> Dict[str, Tuple[str, str]]:
    indexed: Dict[str, Tuple[str, str]] = {}
    for raw_name, raw_value in environment.items():
        if not isinstance(raw_name, str) or not raw_name:
            raise FingerprintError("environment contains an invalid variable name")
        if not isinstance(raw_value, str):
            raise FingerprintError("environment variable {!r} is not a string".format(raw_name))
        folded = raw_name.casefold()
        if folded in indexed:
            raise FingerprintError(
                "environment contains ambiguous variables {!r} and {!r}".format(
                    indexed[folded][0], raw_name
                )
            )
        indexed[folded] = (raw_name, raw_value)
    return indexed


def _environment_value(
    indexed: Mapping[str, Tuple[str, str]], name: str, *, required: bool
) -> Optional[str]:
    entry = indexed.get(name.casefold())
    if entry is None:
        if required:
            raise FingerprintError("required environment variable {} is missing".format(name))
        return None
    value = entry[1]
    if not value or value != value.strip():
        if required:
            raise FingerprintError("required environment variable {} is empty".format(name))
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise FingerprintError("environment variable {} contains a control character".format(name))
    return value


def _has_reparse_attribute(file_stat: os.stat_result) -> bool:
    return bool(getattr(file_stat, "st_file_attributes", 0) & _REPARSE_POINT)


def _require_regular_file(path_value: Any, label: str) -> Path:
    try:
        path = Path(path_value)
    except (TypeError, ValueError) as exc:
        raise FingerprintError("{} is not a valid filesystem path".format(label)) from exc
    if not path.is_absolute():
        raise FingerprintError("{} is not an absolute path: {}".format(label, path))
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise FingerprintError("{} cannot be inspected: {}".format(label, exc)) from exc
    if path.is_symlink() or _has_reparse_attribute(file_stat):
        raise FingerprintError("{} must not be a symlink or reparse point: {}".format(label, path))
    if not stat.S_ISREG(file_stat.st_mode):
        raise FingerprintError("{} is not a regular file: {}".format(label, path))
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise FingerprintError("{} cannot be resolved: {}".format(label, exc)) from exc


def _require_directory(path_value: Any, label: str) -> Path:
    try:
        path = Path(path_value)
    except (TypeError, ValueError) as exc:
        raise FingerprintError("{} is not a valid filesystem path".format(label)) from exc
    if not path.is_absolute():
        raise FingerprintError("{} is not an absolute path: {}".format(label, path))
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise FingerprintError("{} cannot be inspected: {}".format(label, exc)) from exc
    if path.is_symlink() or _has_reparse_attribute(file_stat):
        raise FingerprintError("{} must not be a symlink or reparse point: {}".format(label, path))
    if not stat.S_ISDIR(file_stat.st_mode):
        raise FingerprintError("{} is not a directory: {}".format(label, path))
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise FingerprintError("{} cannot be resolved: {}".format(label, exc)) from exc


def _stat_identity(file_stat: os.stat_result) -> Tuple[int, int, int, int, int]:
    # Windows path-based stat synthesizes executable permission bits from the
    # suffix, while fstat on the already-open handle does not.  Compare the
    # actual file type and stable identity fields, not those synthetic bits.
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        stat.S_IFMT(file_stat.st_mode),
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


def _fingerprint_file(path_value: Any, label: str) -> Dict[str, Any]:
    path = _require_regular_file(path_value, label)
    digest = hashlib.sha256()
    try:
        before_path = path.lstat()
        with path.open("rb") as source:
            before_handle = os.fstat(source.fileno())
            if _stat_identity(before_path) != _stat_identity(before_handle):
                raise FingerprintError("{} changed before hashing".format(label))
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
            after_handle = os.fstat(source.fileno())
        after_path = path.lstat()
    except FingerprintError:
        raise
    except OSError as exc:
        raise FingerprintError("{} could not be hashed: {}".format(label, exc)) from exc
    if (
        _stat_identity(before_handle) != _stat_identity(after_handle)
        or _stat_identity(before_path) != _stat_identity(after_path)
    ):
        raise FingerprintError("{} changed while it was being hashed".format(label))
    return {
        "bytes": before_handle.st_size,
        "path": str(path),
        "sha256": digest.hexdigest(),
    }


def _decode_utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise FingerprintError("{} is not valid UTF-8".format(label)) from exc


def _decode_native(data: bytes, label: str) -> str:
    encoding = locale.getpreferredencoding(False) or "utf-8"
    try:
        return data.decode(encoding, errors="strict")
    except UnicodeDecodeError as exc:
        raise FingerprintError("{} is not valid {}".format(label, encoding)) from exc


def _canonical_command_output(data: bytes, label: str) -> str:
    text = _decode_native(data, label).replace("\r\n", "\n").replace("\r", "\n")
    text = text.rstrip("\n")
    for character in text:
        if character not in "\n\t" and (ord(character) < 0x20 or ord(character) == 0x7F):
            raise FingerprintError("{} contains a control character".format(label))
    return text


def find_vswhere(environment: Mapping[str, str]) -> Path:
    indexed = _index_environment(environment)
    program_files = _environment_value(indexed, "ProgramFiles(x86)", required=True)
    assert program_files is not None
    candidate = (
        Path(program_files)
        / "Microsoft Visual Studio"
        / "Installer"
        / "vswhere.exe"
    )
    return _require_regular_file(candidate, "vswhere.exe")


def _query_visual_studio(
    vswhere: Path, environment: Mapping[str, str], runner: CommandRunner
) -> Tuple[Path, str]:
    result = runner(
        [
            str(vswhere),
            "-products",
            "*",
            "-requires",
            _VC_TOOLS_COMPONENT,
            "-format",
            "json",
            "-utf8",
        ],
        environment,
    )
    if result.returncode != 0:
        raise FingerprintError("vswhere.exe failed with exit code {}".format(result.returncode))
    if result.stderr:
        raise FingerprintError("vswhere.exe produced unexpected stderr")
    try:
        instances = json.loads(_decode_utf8(result.stdout, "vswhere.exe output"))
    except json.JSONDecodeError as exc:
        raise FingerprintError("vswhere.exe did not return valid JSON") from exc
    if not isinstance(instances, list) or len(instances) != 1:
        count = len(instances) if isinstance(instances, list) else "non-list"
        raise FingerprintError(
            "expected exactly one Visual Studio installation, found {}".format(count)
        )
    instance = instances[0]
    if not isinstance(instance, dict):
        raise FingerprintError("the Visual Studio instance is not a JSON object")
    if instance.get("isComplete") is not True or instance.get("isLaunchable") is not True:
        raise FingerprintError("the Visual Studio installation is incomplete or not launchable")
    installation_version = _clean_scalar(
        instance.get("installationVersion"), "Visual Studio installationVersion"
    )
    installation_path = _require_directory(
        instance.get("installationPath"), "Visual Studio installationPath"
    )
    return installation_path, installation_version


def _decode_vsdevcmd_environment(data: bytes) -> Dict[str, str]:
    if len(data) % 2:
        raise FingerprintError("VsDevCmd environment has an invalid UTF-16LE byte count")
    try:
        text = data.decode("utf-16le", errors="strict")
    except UnicodeDecodeError as exc:
        raise FingerprintError("VsDevCmd environment is not valid UTF-16LE") from exc
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    captured: Dict[str, str] = {}
    folded_names: Dict[str, str] = {}
    for line in text.split("\n"):
        if not line:
            continue
        # cmd.exe exposes drive-current-directory pseudo variables as '=C:=...'.
        if line.startswith("="):
            continue
        if "=" not in line:
            raise FingerprintError("VsDevCmd environment contains a malformed line")
        name, value = line.split("=", 1)
        if not name or any(ord(character) < 0x20 for character in name):
            raise FingerprintError("VsDevCmd environment contains an invalid variable name")
        folded = name.casefold()
        if folded in folded_names:
            raise FingerprintError(
                "VsDevCmd environment contains ambiguous variables {!r} and {!r}".format(
                    folded_names[folded], name
                )
            )
        folded_names[folded] = name
        captured[name] = value
    if not captured:
        raise FingerprintError("VsDevCmd environment is empty")
    return captured


def _capture_vs_environment(
    vsdevcmd: Path, base_environment: Mapping[str, str], runner: CommandRunner
) -> Dict[str, str]:
    indexed = _index_environment(base_environment)
    comspec_value = _environment_value(indexed, "ComSpec", required=True)
    assert comspec_value is not None
    comspec = _require_regular_file(comspec_value, "ComSpec")
    if '"' in str(vsdevcmd) or '"' in str(comspec):
        raise FingerprintError("VsDevCmd and ComSpec paths cannot contain a double quote")
    # Running VsDevCmd under ``cmd /u`` also changes the encoding used by commands
    # executed inside the batch file.  Visual Studio's setup scripts use temporary
    # command output internally and fail on GitHub's hosted image when that output is
    # forced to UTF-16.  Initialize the toolchain in a normal outer cmd.exe, then use
    # a nested Unicode cmd.exe only for the final deterministic environment dump.
    batch_command = (
        'call "{}" -no_logo -arch=amd64 -host_arch=amd64 >nul '
        '&& "{}" /d /u /c set'.format(vsdevcmd, comspec)
    )
    result = runner(
        [str(comspec), "/d", "/s", "/c", batch_command], base_environment
    )
    if result.returncode != 0:
        raise FingerprintError("VsDevCmd failed with exit code {}".format(result.returncode))
    if result.stderr:
        raise FingerprintError("VsDevCmd produced unexpected stderr")
    return _decode_vsdevcmd_environment(result.stdout)


def _required_captured_environment(
    environment: Mapping[str, str], name: str
) -> str:
    indexed = _index_environment(environment)
    value = _environment_value(indexed, name, required=True)
    assert value is not None
    return value


def _locate_tool(
    tool_name: str, environment: Mapping[str, str], runner: CommandRunner
) -> Path:
    indexed = _index_environment(environment)
    system_root_value = _environment_value(indexed, "SystemRoot", required=True)
    assert system_root_value is not None
    where_exe = _require_regular_file(
        Path(system_root_value) / "System32" / "where.exe", "where.exe"
    )
    result = runner([str(where_exe), tool_name], environment)
    if result.returncode != 0:
        raise FingerprintError(
            "where.exe could not locate {} (exit code {})".format(
                tool_name, result.returncode
            )
        )
    if result.stderr:
        raise FingerprintError("where.exe produced stderr while locating {}".format(tool_name))
    output = _decode_native(result.stdout, "where.exe output for {}".format(tool_name))
    paths = [line.strip() for line in output.replace("\r", "\n").split("\n") if line.strip()]
    if len(paths) != 1:
        raise FingerprintError(
            "expected exactly one {} path, found {}".format(tool_name, len(paths))
        )
    return _require_regular_file(paths[0], tool_name)


def _path_key(path: Path) -> str:
    return ntpath.normcase(str(path.resolve(strict=True)))


def _is_within(path: Path, directory: Path) -> bool:
    path_key = _path_key(path)
    directory_key = _path_key(directory).rstrip("\\/")
    return path_key == directory_key or path_key.startswith(directory_key + "\\")


def _tool_record(
    path: Path,
    arguments: Sequence[str],
    accepted_returncodes: Sequence[int],
    environment: Mapping[str, str],
    runner: CommandRunner,
    label: str,
) -> Dict[str, Any]:
    result = runner([str(path)] + list(arguments), environment)
    if result.returncode not in accepted_returncodes:
        raise FingerprintError(
            "{} version command failed with exit code {}".format(label, result.returncode)
        )
    stdout = _canonical_command_output(result.stdout, "{} stdout".format(label))
    stderr = _canonical_command_output(result.stderr, "{} stderr".format(label))
    combined = "{}\n{}".format(stdout, stderr).casefold()
    if "version" not in combined:
        raise FingerprintError("{} version command did not identify a version".format(label))
    record = _fingerprint_file(path, label)
    record["versionCommand"] = {
        "arguments": list(arguments),
        "returnCode": result.returncode,
        "stderr": stderr,
        "stdout": stdout,
    }
    return record


def _python_record() -> Dict[str, Any]:
    implementation = platform.python_implementation()
    if implementation != "CPython" or sys.implementation.name != "cpython":
        raise FingerprintError("wheel fingerprinting requires CPython")
    compiler = _clean_scalar(platform.python_compiler(), "CPython compiler")
    version = _clean_scalar(sys.version, "CPython sys.version")
    cache_tag = _clean_scalar(sys.implementation.cache_tag, "CPython cache tag")
    executable = _fingerprint_file(sys.executable, "CPython executable")
    version_info = sys.version_info
    return {
        "cacheTag": cache_tag,
        "compiler": compiler,
        "executable": executable,
        "hexVersion": sys.hexversion,
        "implementation": implementation,
        "pointerBits": 8 * __import__("struct").calcsize("P"),
        "sysVersion": version,
        "versionInfo": {
            "major": version_info.major,
            "micro": version_info.micro,
            "minor": version_info.minor,
            "releaseLevel": version_info.releaselevel,
            "serial": version_info.serial,
        },
    }


def _canonical_package_name(name: str) -> str:
    return _PACKAGE_NAME_RE.sub("-", name).lower()


def _package_versions() -> Dict[str, Dict[str, str]]:
    targets = {"setuptools", "wheel"}
    matches: Dict[str, list] = {name: [] for name in targets}
    try:
        distributions = list(importlib_metadata.distributions())
    except Exception as exc:  # importlib backends may raise non-OSError exceptions.
        raise FingerprintError("installed Python distributions could not be enumerated") from exc
    for distribution in distributions:
        try:
            distribution_name = distribution.metadata.get("Name")
        except Exception as exc:
            raise FingerprintError("installed distribution metadata could not be read") from exc
        if not isinstance(distribution_name, str) or not distribution_name:
            continue
        canonical_name = _canonical_package_name(distribution_name)
        if canonical_name in targets:
            matches[canonical_name].append(distribution)
    result: Dict[str, Dict[str, str]] = {}
    for name in sorted(targets):
        candidates = matches[name]
        if len(candidates) != 1:
            raise FingerprintError(
                "expected exactly one installed {} distribution, found {}".format(
                    name, len(candidates)
                )
            )
        try:
            version = candidates[0].version
        except Exception as exc:
            raise FingerprintError("{} version could not be read".format(name)) from exc
        result[name] = {"version": _clean_scalar(version, "{} version".format(name))}
    return result


def collect_windows_fingerprint(
    environment: Optional[Mapping[str, str]] = None,
    runner: CommandRunner = run_command,
) -> Dict[str, Any]:
    """Collect a complete Windows wheel-build fingerprint or raise."""

    if not _is_windows():
        raise FingerprintError("Windows wheel fingerprinting is only supported on Windows")
    source_environment = dict(os.environ if environment is None else environment)
    indexed = _index_environment(source_environment)
    image_os = _environment_value(indexed, "ImageOS", required=True)
    image_version = _environment_value(indexed, "ImageVersion", required=True)
    assert image_os is not None and image_version is not None

    provisioner: Dict[str, str] = {}
    for output_name, environment_name in (
        ("architecture", "RUNNER_ARCH"),
        ("environment", "RUNNER_ENVIRONMENT"),
        ("imageProvisioner", "ImageProvisioner"),
        ("operatingSystem", "RUNNER_OS"),
    ):
        value = _environment_value(indexed, environment_name, required=False)
        if value is not None:
            provisioner[output_name] = value

    vswhere = find_vswhere(source_environment)
    installation_path, installation_version = _query_visual_studio(
        vswhere, source_environment, runner
    )
    vsdevcmd = _require_regular_file(
        installation_path / "Common7" / "Tools" / "VsDevCmd.bat", "VsDevCmd.bat"
    )
    vc_environment = _capture_vs_environment(vsdevcmd, source_environment, runner)

    host_architecture = _required_captured_environment(
        vc_environment, "VSCMD_ARG_HOST_ARCH"
    )
    target_architecture = _required_captured_environment(
        vc_environment, "VSCMD_ARG_TGT_ARCH"
    )
    if host_architecture.casefold() not in {"amd64", "x64"}:
        raise FingerprintError("VsDevCmd did not select an amd64 host architecture")
    if target_architecture.casefold() not in {"amd64", "x64"}:
        raise FingerprintError("VsDevCmd did not select an amd64 target architecture")

    vc_tools_version = _required_captured_environment(vc_environment, "VCToolsVersion")
    windows_sdk_version = _required_captured_environment(vc_environment, "WindowsSDKVersion")
    vsdevcmd_version = _required_captured_environment(vc_environment, "VSCMD_VER")
    vc_tools_directory = _require_directory(
        _required_captured_environment(vc_environment, "VCToolsInstallDir"),
        "VCToolsInstallDir",
    )
    if vc_tools_directory.name.casefold() != vc_tools_version.rstrip("\\/").casefold():
        raise FingerprintError("VCToolsVersion does not match VCToolsInstallDir")
    if not _is_within(vc_tools_directory, installation_path):
        raise FingerprintError("VCToolsInstallDir is outside the selected Visual Studio instance")

    cl_path = _locate_tool("cl.exe", vc_environment, runner)
    link_path = _locate_tool("link.exe", vc_environment, runner)
    if _path_key(cl_path) == _path_key(link_path):
        raise FingerprintError("cl.exe and link.exe resolve to the same file")
    for label, tool_path in (("cl.exe", cl_path), ("link.exe", link_path)):
        if not _is_within(tool_path, vc_tools_directory):
            raise FingerprintError("{} is outside VCToolsInstallDir".format(label))

    return {
        "python": {
            **_python_record(),
            "packages": _package_versions(),
        },
        "runner": {
            "imageOS": image_os,
            "imageVersion": image_version,
            "provisioner": provisioner,
        },
        "schemaVersion": SCHEMA_VERSION,
        "tools": {
            "cl": _tool_record(
                cl_path, ["/Bv"], [0, 2], vc_environment, runner, "cl.exe"
            ),
            "link": _tool_record(
                link_path, ["/?"], [0], vc_environment, runner, "link.exe"
            ),
        },
        "visualStudio": {
            "hostArchitecture": host_architecture,
            "installationPath": str(installation_path),
            "installationVersion": installation_version,
            "targetArchitecture": target_architecture,
            "vcToolsInstallDir": str(vc_tools_directory),
            "vcToolsVersion": vc_tools_version,
            "vsDevCmdPath": str(vsdevcmd),
            "vsDevCmdVersion": vsdevcmd_version,
            "vswhere": _fingerprint_file(vswhere, "vswhere.exe"),
            "windowsSDKVersion": windows_sdk_version,
        },
    }


def _validate_json_value(value: Any, label: str = "document") -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, "{}[{}]".format(label, index))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise FingerprintError("{} contains a non-string JSON key".format(label))
            _validate_json_value(item, "{}.{}".format(label, key))
        return
    raise FingerprintError("{} contains a non-canonical JSON value".format(label))


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    _validate_json_value(document)
    return (
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")


def fingerprint_sha256(document: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


def _normalize_expected_sha256(expected_sha256: Optional[str]) -> Optional[str]:
    if expected_sha256 is None:
        return None
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        raise FingerprintError("expected SHA-256 must contain exactly 64 hexadecimal characters")
    return expected_sha256.lower()


def write_fingerprint(
    output: Path,
    document: Mapping[str, Any],
    expected_sha256: Optional[str] = None,
) -> str:
    payload = canonical_json_bytes(document)
    digest = hashlib.sha256(payload).hexdigest()
    expected = _normalize_expected_sha256(expected_sha256)
    if expected is not None and digest != expected:
        raise FingerprintError(
            "fingerprint SHA-256 mismatch: expected {}, got {}".format(expected, digest)
        )
    output_path = Path(output)
    try:
        with output_path.open("xb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
    except FileExistsError as exc:
        raise FingerprintError("refusing to overwrite {}".format(output_path)) from exc
    except OSError as exc:
        raise FingerprintError("could not write {}: {}".format(output_path, exc)) from exc
    return digest


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fingerprint the exact Windows toolchain used for Atomic-Stockfish wheels."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-sha256")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        document = collect_windows_fingerprint()
        digest = write_fingerprint(args.output, document, args.expected_sha256)
    except (FingerprintError, OSError, ValueError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
