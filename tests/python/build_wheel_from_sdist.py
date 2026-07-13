from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: build_wheel_from_sdist.py SDIST_DIRECTORY WHEEL_DIRECTORY")

    source_directory = Path(sys.argv[1]).resolve()
    output_directory = Path(sys.argv[2]).resolve()
    source_distributions = sorted(source_directory.glob("*.tar.gz"))
    if len(source_distributions) != 1:
        fail(
            f"expected exactly one sdist in {source_directory}, "
            f"found {len(source_distributions)}"
        )

    with tarfile.open(source_distributions[0], "r:gz") as archive:
        members = archive.getnames()
    required_suffixes = (
        "/pyffish.pyi",
        "/src/atomic_init.h",
        "/src/api/atomic_board.h",
        "/src/position.h",
        "/src/syzygy/tbprobe.h",
    )
    missing = [
        suffix.lstrip("/")
        for suffix in required_suffixes
        if not any(member.endswith(suffix) for member in members)
    ]
    if missing:
        fail(f"sdist omits required binding inputs: {', '.join(missing)}")

    output_directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-deps",
            "--wheel-dir",
            str(output_directory),
            str(source_distributions[0]),
        ],
        check=True,
    )

    wheels = sorted(output_directory.glob("*.whl"))
    if len(wheels) != 1:
        fail(f"expected exactly one wheel in {output_directory}, found {len(wheels)}")

    print(f"Built Atomic pyffish wheel from sdist: {wheels[0].name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
