from __future__ import annotations

import subprocess
import sys
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
