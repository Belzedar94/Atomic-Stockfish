"""The default network: one name, one published asset, one SHA-256.

evaluate.h names the default network (EvalFileDefaultName). src/Makefile says
where it is published and what its SHA-256 is (NNUE_NET_*), and `make build` /
`make profile-build` fetch it through scripts/net.sh and embed it. These
checks keep the pieces in step when the default network changes.
"""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = (ROOT / "src" / "Makefile").read_text(encoding="utf-8")
EVALUATE_H = (ROOT / "src" / "evaluate.h").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
NET_SH = (ROOT / "scripts" / "net.sh").read_text(encoding="utf-8")
NATIVE_RELEASE = (ROOT / "scripts" / "build_atomic_native_release.sh").read_text(
    encoding="utf-8"
)


def make_variable(name: str) -> str:
    match = re.search(rf"^{name}\s*:?=\s*(.*?)\s*$", MAKEFILE, re.MULTILINE)
    assert match is not None, f"{name} is not set in src/Makefile"
    return match.group(1)


def default_name() -> str:
    match = re.search(
        r'^#define EvalFileDefaultName "([^"]+)"', EVALUATE_H, re.MULTILINE
    )
    assert match is not None
    return match.group(1)


def test_makefile_reads_the_network_name_from_evaluate_h():
    assert make_variable("NNUE_NET_NAME") == (
        "$(shell sed -n 's/^.define EvalFileDefaultName \"\\([^\"]*\\)\".*/\\1/p' evaluate.h)"
    )
    # The same expression, applied to evaluate.h line by line.
    names = [
        m.group(1)
        for line in EVALUATE_H.splitlines()
        if (m := re.match(r'^.define EvalFileDefaultName "([^"]*)"', line))
    ]
    assert names == [default_name()]


def test_published_asset_is_the_default_network():
    tag = make_variable("NNUE_NET_TAG")
    assert re.fullmatch(r"[A-Za-z0-9._-]+", tag)
    assert make_variable("NNUE_NET_URL") == (
        "https://github.com/Belzedar94/Atomic-Stockfish/releases/download/"
        "$(NNUE_NET_TAG)/$(NNUE_NET_NAME)"
    )
    assert default_name().endswith(".nnue")


def test_sha256_is_well_formed_and_matches_the_readme():
    sha = make_variable("NNUE_NET_SHA256")
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    readme = re.search(
        r"is `"
        + re.escape(default_name())
        + r"`,\s+identified by SHA-256\s+`([0-9A-Fa-f]{64})`",
        README,
    )
    assert readme is not None, "README must name the default network and its SHA-256"
    assert readme.group(1).lower() == sha


def test_openbench_embeds_under_the_default_name():
    assert make_variable("OPENBENCH_NET") == default_name()


def test_build_and_profile_build_fetch_the_network_first():
    assert re.search(r"^build: \$\(NET_DEPENDENCY\) config-sanity$", MAKEFILE, re.M)
    assert re.search(
        r"^profile-build: \$\(NET_DEPENDENCY\) config-sanity objclean profileclean$",
        MAKEFILE,
        re.M,
    )
    assert re.search(
        r"^data-generator-profile-build: \$\(NET_DEPENDENCY\) config-sanity",
        MAKEFILE,
        re.M,
    )
    assert re.search(r"^net:\n\t@\$\(NET_SH_ENV\) \$\(SHELL\) \.\./scripts/net\.sh$", MAKEFILE, re.M)
    assert make_variable("embed") == "yes"


def test_net_script_verifies_before_it_replaces_the_file():
    # Download to a temporary file, check it, then rename it into place.
    assert 'download "$NET_URL" "$tmp"' in NET_SH
    assert 'validate_network "$tmp"' in NET_SH
    assert 'mv "$tmp" "$NET_NAME"' in NET_SH
    # A network supplied by OpenBench is copied as it is, never re-checked.
    evalfile = NET_SH.index('if [ -n "$EVALFILE" ]; then')
    assert evalfile < NET_SH.index('validate_network "$NET_NAME"')


def test_release_assets_stay_network_free():
    assert NATIVE_RELEASE.count("embed=no build") == 2
