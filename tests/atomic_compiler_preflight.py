#!/usr/bin/env python3
"""Bind normative engine comparisons to immutable, like-for-like binaries."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import subprocess
from typing import Callable, Iterable, Mapping, Optional, Tuple


SETTINGS_RE = re.compile(
    r"^\s*Compilation\s+settings(?:\s+include)?\s*:\s*(.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
COMPILED_BY_RE = re.compile(
    r"^\s*Compiled\s+by\s*(?::\s*)?(.*?)\s+on\s+.+?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
VERSION_MACRO_RE = re.compile(
    r"^\s*(?:Compiler\s+)?__VERSION__\s+macro"
    r"(?:\s+expands\s+to)?\s*:\s*(.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
COMPILER_IDENTITY_RE = re.compile(
    r"^(?P<family>.+?)\s+(?P<version>\d+(?:\.\d+)+(?:[-+._A-Za-z0-9]*))$"
)

BITNESS_TOKENS = frozenset({"32BIT", "64BIT"})
ISA_ORDER = (
    "AVX512ICL",
    "VNNI",
    "AVX512",
    "BMI2",
    "AVX2",
    "SSE41",
    "SSSE3",
    "SSE2",
    "POPCNT",
    "MMX",
    "NEON_DOTPROD",
    "NEON",
    "LASX",
    "LSX",
)
ISA_TOKENS = frozenset(ISA_ORDER)
BUILD_MODE_TOKENS = frozenset({"DEBUG"})

NORMATIVE_BITNESS = "64bit"
NORMATIVE_ISA = frozenset({"BMI2", "AVX2", "SSE41", "SSSE3", "SSE2", "POPCNT"})
NORMATIVE_BASELINE_SHA256 = (
    "4EACAAB40DCA84F5A255EA57231F2795D43B5DDA85CE50EBBA1A1B2937B46331"
)


class CompilerPreflightError(RuntimeError):
    """Raised when an engine or comparison artifact is not normative."""


@dataclass(frozen=True)
class FileFingerprint:
    label: str
    path: Path
    size: int
    sha256: str

    def display(self) -> str:
        return (
            f"{self.label}_bytes={self.size} "
            f"{self.label}_sha256={self.sha256}"
        )


@dataclass(frozen=True)
class CompilationSignature:
    bitness: str
    isa: frozenset[str]
    compiler_family: str
    compiler_version: str
    version_macro: str
    debug: bool = False

    def display(self) -> str:
        features = [feature for feature in ISA_ORDER if feature in self.isa]
        build_mode = ["DEBUG"] if self.debug else []
        return " ".join((self.bitness, *features, *build_mode))

    def compiler_display(self) -> str:
        return f"{self.compiler_family} {self.compiler_version}"


def _single_match(pattern: re.Pattern[str], output: str, label: str, field: str) -> str:
    matches = pattern.findall(output)
    if not matches:
        raise CompilerPreflightError(
            f"{label} compiler output has no {field} line"
        )
    if len(matches) != 1:
        raise CompilerPreflightError(
            f"{label} compiler output has {len(matches)} {field} lines"
        )
    value = " ".join(matches[0].split())
    if not value:
        raise CompilerPreflightError(f"{label} {field} line is empty")
    return value


def parse_compilation_settings(
    output: str, *, label: str = "engine"
) -> CompilationSignature:
    """Parse target and compiler identity from the real ``compiler`` output."""

    settings = _single_match(
        SETTINGS_RE, output, label, "Compilation settings"
    )
    compiled_by = _single_match(COMPILED_BY_RE, output, label, "Compiled by")
    version_macro = _single_match(
        VERSION_MACRO_RE, output, label, "__VERSION__ macro"
    )

    identity = COMPILER_IDENTITY_RE.fullmatch(compiled_by)
    if identity is None:
        raise CompilerPreflightError(
            f"{label} Compiled by line has no parseable family/version: {compiled_by}"
        )
    compiler_family = identity.group("family")
    compiler_version = identity.group("version")
    if compiler_version != version_macro:
        raise CompilerPreflightError(
            f"{label} compiler version mismatch: Compiled by={compiler_version} "
            f"__VERSION__={version_macro}"
        )

    tokens = [token.upper() for token in settings.split()]
    if len(tokens) != len(set(tokens)):
        raise CompilerPreflightError(
            f"{label} Compilation settings contains duplicate tokens"
        )

    bitness = [token for token in tokens if token in BITNESS_TOKENS]
    if len(bitness) != 1:
        raise CompilerPreflightError(
            f"{label} Compilation settings must contain exactly one of 32bit/64bit"
        )

    unknown = sorted(
        set(tokens) - BITNESS_TOKENS - ISA_TOKENS - BUILD_MODE_TOKENS
    )
    if unknown:
        raise CompilerPreflightError(
            f"{label} Compilation settings has unknown tokens: {', '.join(unknown)}"
        )

    normalized_bitness = "64bit" if bitness[0] == "64BIT" else "32bit"
    return CompilationSignature(
        bitness=normalized_bitness,
        isa=frozenset(token for token in tokens if token in ISA_TOKENS),
        compiler_family=compiler_family,
        compiler_version=compiler_version,
        version_macro=version_macro,
        debug="DEBUG" in tokens,
    )


def fingerprint_file(path: Path, *, label: str) -> FileFingerprint:
    """Read one file completely and return its immutable comparison identity."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise CompilerPreflightError(f"{label} does not exist: {resolved}")
    digest = hashlib.sha256()
    try:
        with resolved.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        size = resolved.stat().st_size
    except OSError as exc:
        raise CompilerPreflightError(
            f"could not fingerprint {label} {resolved}: {exc}"
        ) from exc
    return FileFingerprint(label, resolved, size, digest.hexdigest().upper())


def fingerprint_files(
    named_paths: Iterable[Tuple[str, Path]],
) -> Tuple[FileFingerprint, ...]:
    """Fingerprint all named inputs before any benchmark or match process starts."""

    fingerprints = tuple(
        fingerprint_file(path, label=label) for label, path in named_paths
    )
    labels = [fingerprint.label for fingerprint in fingerprints]
    if len(labels) != len(set(labels)):
        raise CompilerPreflightError("artifact fingerprint labels must be unique")
    return fingerprints


def require_sha256(
    fingerprint: FileFingerprint, expected_sha256: str, *, description: str
) -> None:
    expected = expected_sha256.upper()
    if fingerprint.sha256 != expected:
        raise CompilerPreflightError(
            f"unexpected {description} SHA-256: {fingerprint.sha256}; expected {expected}"
        )


def verify_file_fingerprints(
    expected: Iterable[FileFingerprint],
    *,
    emit: Optional[Callable[[str], None]] = None,
    pass_label: str = "Artifact postflight",
) -> None:
    """Fail if any path, byte count, or SHA changed after the workload."""

    expected_tuple = tuple(expected)
    for before in expected_tuple:
        try:
            after = fingerprint_file(before.path, label=before.label)
        except CompilerPreflightError as exc:
            raise CompilerPreflightError(
                f"{before.label} changed after preflight: {exc}"
            ) from exc
        if after != before:
            raise CompilerPreflightError(
                f"{before.label} changed after preflight: "
                f"before_bytes={before.size} before_sha256={before.sha256} "
                f"after_bytes={after.size} after_sha256={after.sha256}"
            )
    if emit is not None:
        emit(f"{pass_label}: PASS files={len(expected_tuple)}")


def probe_compilation_settings(
    executable: Path, *, label: str, timeout: float = 10.0
) -> CompilationSignature:
    """Run one engine briefly and parse its target and compiler identity."""

    path = executable.expanduser().resolve()
    if not path.is_file():
        raise CompilerPreflightError(f"{label} executable does not exist: {path}")
    if timeout <= 0:
        raise CompilerPreflightError("compiler preflight timeout must be positive")

    try:
        completed = subprocess.run(
            [str(path)],
            input="compiler\nquit\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except OSError as exc:
        raise CompilerPreflightError(
            f"could not start {label} executable {path}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CompilerPreflightError(
            f"{label} compiler probe timed out after {timeout:g}s: {path}"
        ) from exc

    if completed.returncode != 0:
        raise CompilerPreflightError(
            f"{label} compiler probe exited with {completed.returncode}: {path}"
        )
    return parse_compilation_settings(completed.stdout, label=label)


def _require_normative_target(signature: CompilationSignature, *, label: str) -> None:
    if (
        signature.bitness != NORMATIVE_BITNESS
        or signature.isa != NORMATIVE_ISA
        or signature.debug
    ):
        expected = CompilationSignature(
            NORMATIVE_BITNESS,
            NORMATIVE_ISA,
            signature.compiler_family,
            signature.compiler_version,
            signature.version_macro,
        )
        raise CompilerPreflightError(
            f"{label} is not the exact normative BMI2 release target: "
            f"actual=[{signature.display()}] expected=[{expected.display()}]"
        )


def require_matching_compilation_settings(
    candidate: Path,
    baseline: Path,
    *,
    timeout: float = 10.0,
    emit: Callable[[str], None] = print,
    expected_fingerprints: Optional[Mapping[str, FileFingerprint]] = None,
    expected_baseline_sha256: Optional[str] = None,
) -> CompilationSignature:
    """Require the exact BMI2 target, compiler identity, and stable binaries."""

    if expected_fingerprints is None:
        initial = fingerprint_files(
            (("candidate", candidate), ("baseline", baseline))
        )
        fingerprints = {item.label: item for item in initial}
    else:
        fingerprints = dict(expected_fingerprints)
        try:
            initial = (fingerprints["candidate"], fingerprints["baseline"])
        except KeyError as exc:
            raise CompilerPreflightError(
                "expected fingerprints must contain candidate and baseline"
            ) from exc
        resolved_paths = {
            "candidate": candidate.expanduser().resolve(),
            "baseline": baseline.expanduser().resolve(),
        }
        for label, path in resolved_paths.items():
            if fingerprints[label].path != path:
                raise CompilerPreflightError(
                    f"{label} fingerprint path mismatch: "
                    f"{fingerprints[label].path} != {path}"
                )
        verify_file_fingerprints(initial)

    if expected_baseline_sha256 is not None:
        require_sha256(
            fingerprints["baseline"],
            expected_baseline_sha256,
            description="frozen Fairy-Stockfish BMI2 baseline",
        )

    candidate_signature = probe_compilation_settings(
        candidate, label="candidate", timeout=timeout
    )
    baseline_signature = probe_compilation_settings(
        baseline, label="baseline", timeout=timeout
    )
    verify_file_fingerprints(initial)

    _require_normative_target(candidate_signature, label="candidate")
    _require_normative_target(baseline_signature, label="baseline")

    if candidate_signature.compiler_family != baseline_signature.compiler_family:
        raise CompilerPreflightError(
            "compiler family mismatch: "
            f"candidate={candidate_signature.compiler_family} "
            f"baseline={baseline_signature.compiler_family}"
        )
    if candidate_signature.compiler_version != baseline_signature.compiler_version:
        raise CompilerPreflightError(
            "compiler version mismatch: "
            f"candidate={candidate_signature.compiler_version} "
            f"baseline={baseline_signature.compiler_version}"
        )
    if candidate_signature.version_macro != baseline_signature.version_macro:
        raise CompilerPreflightError(
            "compiler __VERSION__ mismatch: "
            f"candidate={candidate_signature.version_macro} "
            f"baseline={baseline_signature.version_macro}"
        )

    emit(
        "Compiler preflight: PASS "
        f"signature={candidate_signature.display()} "
        f"compiler={candidate_signature.compiler_display()}"
    )
    emit("Engine artifacts preflight: " + " ".join(item.display() for item in initial))
    return candidate_signature
