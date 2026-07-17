import gzip
from pathlib import Path
import tarfile

import pytest

from scripts.atomic_reproducible_sdist import SdistContractError, normalize_sdist


ROOT = "atomic_pyffish-1.0.2"
EPOCH = 1_700_000_000


def write_sdist(path: Path, *, reverse: bool, mtime: int) -> None:
    entries = [
        (ROOT, None, 0o775),
        (ROOT + "/README.md", b"Atomic\n", 0o664),
        (ROOT + "/src/tool.py", b"print('atomic')\n", 0o775),
    ]
    if reverse:
        entries.reverse()
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for name, payload, mode in entries:
            member = tarfile.TarInfo(name)
            member.mtime = mtime
            member.uid = 1000
            member.gid = 1000
            member.uname = "builder"
            member.gname = "builder"
            member.mode = mode
            if payload is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                import io

                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))


def test_normalized_sdist_is_byte_exact_across_order_and_wall_clock(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    write_sdist(first, reverse=False, mtime=11)
    write_sdist(second, reverse=True, mtime=99)
    output_a = tmp_path / "normalized-a.tar.gz"
    output_b = tmp_path / "normalized-b.tar.gz"

    assert normalize_sdist(first, output_a, ROOT, EPOCH) == normalize_sdist(
        second, output_b, ROOT, EPOCH
    )
    assert output_a.read_bytes() == output_b.read_bytes()
    assert int.from_bytes(output_a.read_bytes()[4:8], "little") == 0

    with tarfile.open(output_a, "r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(
            member.name for member in members
        )
        assert all(member.mtime == EPOCH for member in members)
        assert all(member.uid == member.gid == 0 for member in members)
        assert all(member.uname == member.gname == "" for member in members)
        modes = {member.name: member.mode for member in members}
        assert modes[ROOT] == 0o755
        assert modes[ROOT + "/README.md"] == 0o644
        assert modes[ROOT + "/src/tool.py"] == 0o755


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "other/file",
        ROOT + "\\windows-path",
        ROOT + "//noncanonical",
        ROOT + "/./noncanonical",
    ],
)
def test_normalizer_rejects_unsafe_or_foreign_members(
    tmp_path: Path, name: str
) -> None:
    source = tmp_path / "unsafe.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.size = 0
        archive.addfile(member)
    with pytest.raises(SdistContractError, match="unsafe or foreign"):
        normalize_sdist(source, tmp_path / "output.tar.gz", ROOT, EPOCH)


def test_normalizer_rejects_casefold_collisions(tmp_path: Path) -> None:
    source = tmp_path / "casefold.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        for name in (ROOT + "/README.md", ROOT + "/readme.md"):
            member = tarfile.TarInfo(name)
            member.size = 0
            archive.addfile(member)
    with pytest.raises(SdistContractError, match="case-insensitive duplicate"):
        normalize_sdist(source, tmp_path / "output.tar.gz", ROOT, EPOCH)


def test_normalizer_rejects_symlink_source(tmp_path: Path) -> None:
    target = tmp_path / "target.tar.gz"
    write_sdist(target, reverse=False, mtime=11)
    link = tmp_path / "source.tar.gz"
    try:
        link.symlink_to(target.name)
    except OSError as error:
        pytest.skip("platform cannot create test symlink: %s" % error)

    with pytest.raises(SdistContractError, match="regular file"):
        normalize_sdist(link, tmp_path / "output.tar.gz", ROOT, EPOCH)
