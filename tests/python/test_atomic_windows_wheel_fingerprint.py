import hashlib
import json
import locale
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import atomic_windows_wheel_fingerprint as fingerprint


def _write(path: Path, contents: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    return path.resolve()


def _make_layout(tmp_path: Path) -> SimpleNamespace:
    program_files = tmp_path / "Program Files (x86)"
    vswhere = _write(
        program_files / "Microsoft Visual Studio" / "Installer" / "vswhere.exe",
        b"vswhere-binary",
    )
    system_root = tmp_path / "Windows"
    comspec = _write(system_root / "System32" / "cmd.exe", b"cmd-binary")
    where = _write(system_root / "System32" / "where.exe", b"where-binary")
    installation = (tmp_path / "Microsoft Visual Studio" / "2022" / "Enterprise").resolve()
    installation.mkdir(parents=True)
    vsdevcmd = _write(
        installation / "Common7" / "Tools" / "VsDevCmd.bat", b"@echo off\r\n"
    )
    vc_tools = (installation / "VC" / "Tools" / "MSVC" / "14.44.35207").resolve()
    vc_tools.mkdir(parents=True)
    tool_directory = vc_tools / "bin" / "Hostx64" / "x64"
    cl = _write(tool_directory / "cl.exe", b"cl-binary")
    link = _write(tool_directory / "link.exe", b"link-binary")

    environment = {
        "ComSpec": str(comspec),
        "ImageOS": "win22",
        "ImageVersion": "20260713.1.0",
        "ProgramFiles(x86)": str(program_files.resolve()),
        "RUNNER_ARCH": "X64",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Windows",
        "SystemRoot": str(system_root.resolve()),
    }
    captured = {
        **environment,
        "Path": str(tool_directory.resolve()),
        "VCToolsInstallDir": str(vc_tools),
        "VCToolsVersion": "14.44.35207",
        "VSCMD_ARG_HOST_ARCH": "x64",
        "VSCMD_ARG_TGT_ARCH": "x64",
        "VSCMD_VER": "17.14.14",
        "WindowsSDKVersion": "10.0.26100.0\\",
    }
    instance = {
        "installationPath": str(installation),
        "installationVersion": "17.14.36310.24",
        "isComplete": True,
        "isLaunchable": True,
    }
    return SimpleNamespace(
        captured=captured,
        cl=cl,
        comspec=comspec,
        environment=environment,
        installation=installation,
        instance=instance,
        link=link,
        system_root=system_root.resolve(),
        vc_tools=vc_tools,
        vsdevcmd=vsdevcmd,
        vswhere=vswhere,
        where=where,
    )


def _native_bytes(text: str) -> bytes:
    return text.encode(locale.getpreferredencoding(False) or "utf-8")


def _fake_runner(
    layout: SimpleNamespace,
    *,
    captured=None,
    instances=None,
    where_paths=None,
):
    calls = []
    captured_environment = layout.captured if captured is None else captured
    visual_studio_instances = [layout.instance] if instances is None else instances
    located_paths = {
        "cl.exe": [layout.cl],
        "link.exe": [layout.link],
    }
    if where_paths is not None:
        located_paths.update(where_paths)

    def run(argv, environment=None):
        argv = list(argv)
        calls.append((argv, None if environment is None else dict(environment)))
        executable = Path(argv[0]).resolve()
        if executable == layout.vswhere:
            return fingerprint.CommandResult(
                0, json.dumps(visual_studio_instances).encode("utf-8"), b""
            )
        if executable == layout.comspec:
            lines = "\r\n".join(
                "{}={}".format(name, value)
                for name, value in captured_environment.items()
            )
            return fingerprint.CommandResult(0, (lines + "\r\n").encode("utf-16le"), b"")
        if executable == layout.where:
            paths = located_paths.get(argv[1], [])
            return fingerprint.CommandResult(
                0 if paths else 1,
                _native_bytes("\r\n".join(str(path) for path in paths) + ("\r\n" if paths else "")),
                b"",
            )
        if executable == layout.cl:
            assert argv[1:] == ["/Bv"]
            return fingerprint.CommandResult(
                2,
                b"",
                b"Microsoft (R) C/C++ Optimizing Compiler Version 19.44.35221 for x64\r\n",
            )
        if executable == layout.link:
            assert argv[1:] == ["/?"]
            return fingerprint.CommandResult(
                0,
                b"Microsoft (R) Incremental Linker Version 14.44.35221.0\r\n",
                b"",
            )
        raise AssertionError("unexpected command: {!r}".format(argv))

    return run, calls


def _fake_python_record():
    return {
        "cacheTag": "cpython-313",
        "compiler": "MSC v.1944 64 bit (AMD64)",
        "executable": {
            "bytes": 103192,
            "path": r"C:\hostedtoolcache\windows\Python\3.13.5\x64\python.exe",
            "sha256": "1" * 64,
        },
        "hexVersion": 51185136,
        "implementation": "CPython",
        "pointerBits": 64,
        "sysVersion": "3.13.5 (tags/v3.13.5:6cb20a2, Jun 11 2026, 16:15:46) [MSC v.1944 64 bit (AMD64)]",
        "versionInfo": {
            "major": 3,
            "micro": 5,
            "minor": 13,
            "releaseLevel": "final",
            "serial": 0,
        },
    }


def test_collects_complete_canonical_windows_fingerprint(tmp_path, monkeypatch):
    layout = _make_layout(tmp_path)
    runner, calls = _fake_runner(layout)
    monkeypatch.setattr(fingerprint, "_is_windows", lambda: True)
    monkeypatch.setattr(fingerprint, "_python_record", _fake_python_record)
    monkeypatch.setattr(
        fingerprint,
        "_package_versions",
        lambda: {
            "setuptools": {"version": "80.9.0"},
            "wheel": {"version": "0.46.1"},
        },
    )

    document = fingerprint.collect_windows_fingerprint(layout.environment, runner)

    assert document["schemaVersion"] == 1
    assert document["runner"] == {
        "imageOS": "win22",
        "imageVersion": "20260713.1.0",
        "provisioner": {
            "architecture": "X64",
            "environment": "github-hosted",
            "operatingSystem": "Windows",
        },
    }
    assert document["visualStudio"]["installationVersion"] == "17.14.36310.24"
    assert document["visualStudio"]["vcToolsVersion"] == "14.44.35207"
    assert document["visualStudio"]["windowsSDKVersion"] == "10.0.26100.0\\"
    assert document["python"]["packages"]["wheel"]["version"] == "0.46.1"
    assert document["tools"]["cl"]["sha256"] == hashlib.sha256(b"cl-binary").hexdigest()
    assert document["tools"]["cl"]["versionCommand"]["returnCode"] == 2
    assert "Compiler Version 19.44" in document["tools"]["cl"]["versionCommand"]["stderr"]
    assert document["tools"]["link"]["sha256"] == hashlib.sha256(b"link-binary").hexdigest()
    vsdevcmd_calls = [
        call[0] for call in calls if Path(call[0][0]).resolve() == layout.comspec
    ]
    assert len(vsdevcmd_calls) == 1
    assert vsdevcmd_calls[0][1:4] == ["/d", "/s", "/c"]
    assert "/u" not in vsdevcmd_calls[0][1:4]
    assert '"{}" /d /u /c set'.format(layout.comspec) in vsdevcmd_calls[0][4]
    assert any(call[0][1:] == ["/Bv"] for call in calls if Path(call[0][0]).resolve() == layout.cl)
    assert any(call[0][1:] == ["/?"] for call in calls if Path(call[0][0]).resolve() == layout.link)

    payload = fingerprint.canonical_json_bytes(document)
    assert payload.endswith(b"\n")
    assert payload == fingerprint.canonical_json_bytes(json.loads(payload))
    assert fingerprint.fingerprint_sha256(document) == hashlib.sha256(payload).hexdigest()


def test_rejects_non_windows_before_reading_environment(monkeypatch):
    monkeypatch.setattr(fingerprint, "_is_windows", lambda: False)
    with pytest.raises(fingerprint.FingerprintError, match="only supported on Windows"):
        fingerprint.collect_windows_fingerprint({})


@pytest.mark.parametrize("missing_name", ["ImageOS", "ImageVersion"])
def test_rejects_missing_runner_image_identity(tmp_path, monkeypatch, missing_name):
    layout = _make_layout(tmp_path)
    environment = dict(layout.environment)
    del environment[missing_name]
    monkeypatch.setattr(fingerprint, "_is_windows", lambda: True)
    with pytest.raises(fingerprint.FingerprintError, match=missing_name):
        fingerprint.collect_windows_fingerprint(environment)


def test_rejects_case_insensitive_environment_ambiguity(tmp_path, monkeypatch):
    layout = _make_layout(tmp_path)
    environment = {**layout.environment, "imageos": "win25"}
    monkeypatch.setattr(fingerprint, "_is_windows", lambda: True)
    with pytest.raises(fingerprint.FingerprintError, match="ambiguous variables"):
        fingerprint.collect_windows_fingerprint(environment)


@pytest.mark.parametrize("instances", [[], [{}, {}]])
def test_rejects_zero_or_multiple_visual_studio_instances(tmp_path, instances):
    layout = _make_layout(tmp_path)
    runner, _ = _fake_runner(layout, instances=instances)
    with pytest.raises(fingerprint.FingerprintError, match="exactly one Visual Studio"):
        fingerprint._query_visual_studio(layout.vswhere, layout.environment, runner)


def test_rejects_incomplete_visual_studio_instance(tmp_path):
    layout = _make_layout(tmp_path)
    instance = {**layout.instance, "isComplete": False}
    runner, _ = _fake_runner(layout, instances=[instance])
    with pytest.raises(fingerprint.FingerprintError, match="incomplete or not launchable"):
        fingerprint._query_visual_studio(layout.vswhere, layout.environment, runner)


def test_rejects_visual_studio_instance_with_missing_path(tmp_path):
    layout = _make_layout(tmp_path)
    instance = dict(layout.instance)
    del instance["installationPath"]
    runner, _ = _fake_runner(layout, instances=[instance])
    with pytest.raises(fingerprint.FingerprintError, match="valid filesystem path"):
        fingerprint._query_visual_studio(layout.vswhere, layout.environment, runner)


def test_rejects_duplicate_vsdevcmd_environment_names():
    output = "Path=C:\\one\r\nPATH=C:\\two\r\n".encode("utf-16le")
    with pytest.raises(fingerprint.FingerprintError, match="ambiguous variables"):
        fingerprint._decode_vsdevcmd_environment(output)


def test_ignores_cmd_drive_pseudo_variables():
    output = "=C:=C:\\work\r\nPath=C:\\tools\r\n".encode("utf-16le")
    assert fingerprint._decode_vsdevcmd_environment(output) == {"Path": r"C:\tools"}


def test_rejects_ambiguous_tool_lookup(tmp_path):
    layout = _make_layout(tmp_path)
    second_cl = _write(layout.cl.parent / "second-cl.exe", b"other")
    runner, _ = _fake_runner(layout, where_paths={"cl.exe": [layout.cl, second_cl]})
    with pytest.raises(fingerprint.FingerprintError, match="exactly one cl.exe path"):
        fingerprint._locate_tool("cl.exe", layout.captured, runner)


def test_rejects_tool_output_without_a_version(tmp_path):
    tool = _write(tmp_path / "tool.exe")

    def runner(argv, environment=None):
        return fingerprint.CommandResult(0, b"Microsoft tool banner\r\n", b"")

    with pytest.raises(fingerprint.FingerprintError, match="did not identify a version"):
        fingerprint._tool_record(tool, [], [0], {}, runner, "tool.exe")


@pytest.mark.parametrize(
    "captured_update, expected_message",
    [
        ({"VCToolsVersion": ""}, "VCToolsVersion"),
        ({"WindowsSDKVersion": ""}, "WindowsSDKVersion"),
        ({"VSCMD_ARG_TGT_ARCH": "x86"}, "amd64 target"),
        ({"VSCMD_ARG_HOST_ARCH": "x86"}, "amd64 host"),
    ],
)
def test_rejects_incomplete_or_non_amd64_vs_environment(
    tmp_path, monkeypatch, captured_update, expected_message
):
    layout = _make_layout(tmp_path)
    captured = {**layout.captured, **captured_update}
    runner, _ = _fake_runner(layout, captured=captured)
    monkeypatch.setattr(fingerprint, "_is_windows", lambda: True)
    monkeypatch.setattr(fingerprint, "_python_record", _fake_python_record)
    monkeypatch.setattr(fingerprint, "_package_versions", lambda: {})
    with pytest.raises(fingerprint.FingerprintError, match=expected_message):
        fingerprint.collect_windows_fingerprint(layout.environment, runner)


def test_rejects_non_regular_and_symlinked_files(tmp_path):
    with pytest.raises(fingerprint.FingerprintError, match="not a regular file"):
        fingerprint._require_regular_file(tmp_path.resolve(), "fixture")

    target = _write(tmp_path / "target.exe")
    link = tmp_path / "link.exe"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(fingerprint.FingerprintError, match="symlink or reparse"):
        fingerprint._require_regular_file(link.resolve(strict=False).parent / link.name, "fixture")


class _Distribution:
    def __init__(self, name, version):
        self.metadata = {"Name": name}
        self.version = version


def test_package_versions_are_exact_and_unambiguous(monkeypatch):
    monkeypatch.setattr(
        fingerprint.importlib_metadata,
        "distributions",
        lambda: [
            _Distribution("setuptools", "80.9.0"),
            _Distribution("Wheel", "0.46.1"),
            _Distribution("unrelated", "1.0"),
        ],
    )
    assert fingerprint._package_versions() == {
        "setuptools": {"version": "80.9.0"},
        "wheel": {"version": "0.46.1"},
    }


@pytest.mark.parametrize(
    "distributions, expected_count",
    [
        ([_Distribution("setuptools", "1")], 0),
        (
            [
                _Distribution("setuptools", "1"),
                _Distribution("setuptools", "2"),
                _Distribution("wheel", "1"),
            ],
            2,
        ),
    ],
)
def test_package_versions_reject_missing_or_duplicate_distributions(
    monkeypatch, distributions, expected_count
):
    monkeypatch.setattr(
        fingerprint.importlib_metadata, "distributions", lambda: distributions
    )
    with pytest.raises(fingerprint.FingerprintError, match="found {}".format(expected_count)):
        fingerprint._package_versions()


def test_canonical_json_is_order_independent_and_rejects_floats():
    first = {"z": [3, 2, 1], "a": {"two": 2, "one": 1}}
    second = {"a": {"one": 1, "two": 2}, "z": [3, 2, 1]}
    assert fingerprint.canonical_json_bytes(first) == fingerprint.canonical_json_bytes(second)
    assert fingerprint.fingerprint_sha256(first) == fingerprint.fingerprint_sha256(second)
    with pytest.raises(fingerprint.FingerprintError, match="non-canonical JSON value"):
        fingerprint.canonical_json_bytes({"float": 1.5})


def test_write_is_exclusive_and_checks_expected_digest_before_creation(tmp_path):
    document = {"schemaVersion": 1, "value": "fixture"}
    expected = fingerprint.fingerprint_sha256(document)
    output = tmp_path / "fingerprint.json"
    assert fingerprint.write_fingerprint(output, document, expected.upper()) == expected
    assert output.read_bytes() == fingerprint.canonical_json_bytes(document)
    with pytest.raises(fingerprint.FingerprintError, match="refusing to overwrite"):
        fingerprint.write_fingerprint(output, document)

    mismatch_output = tmp_path / "mismatch.json"
    with pytest.raises(fingerprint.FingerprintError, match="SHA-256 mismatch"):
        fingerprint.write_fingerprint(mismatch_output, document, "0" * 64)
    assert not mismatch_output.exists()

    invalid_output = tmp_path / "invalid.json"
    with pytest.raises(fingerprint.FingerprintError, match="64 hexadecimal"):
        fingerprint.write_fingerprint(invalid_output, document, "not-a-digest")
    assert not invalid_output.exists()


def test_cli_writes_document_and_prints_only_digest(tmp_path, monkeypatch, capsys):
    document = {"schemaVersion": 1, "runner": {"imageOS": "win22"}}
    expected = fingerprint.fingerprint_sha256(document)
    output = tmp_path / "fingerprint.json"
    monkeypatch.setattr(fingerprint, "collect_windows_fingerprint", lambda: document)

    assert fingerprint.main(
        ["--output", str(output), "--expected-sha256", expected]
    ) == 0
    captured = capsys.readouterr()
    assert captured.out == expected + "\n"
    assert captured.err == ""
    assert output.read_bytes() == fingerprint.canonical_json_bytes(document)


def test_cli_mismatch_fails_without_creating_output(tmp_path, monkeypatch, capsys):
    document = {"schemaVersion": 1}
    output = tmp_path / "fingerprint.json"
    monkeypatch.setattr(fingerprint, "collect_windows_fingerprint", lambda: document)

    assert fingerprint.main(
        ["--output", str(output), "--expected-sha256", "0" * 64]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "SHA-256 mismatch" in captured.err
    assert not output.exists()
