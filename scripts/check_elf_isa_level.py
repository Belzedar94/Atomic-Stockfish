#!/usr/bin/env python3
"""Check the x86-64 ISA level an ELF binary declares it needs.

glibc (2.33 and later) reads GNU_PROPERTY_X86_ISA_1_NEEDED from the
.note.gnu.property of every object it loads and refuses to start one whose
level the CPU does not reach ("CPU ISA level is lower than required"). This
script prints that property and checks it against the level an artifact is
meant to run at.

  check_elf_isa_level.py BINARY --max v2     needed level must be <= v2
  check_elf_isa_level.py BINARY --exact v3   needed level must be exactly v3

A binary with no ISA_1_NEEDED property declares no requirement; it passes
--max and fails --exact.
"""

import argparse
import struct
import sys

PT_NOTE = 4
PT_GNU_PROPERTY = 0x6474E553
NT_GNU_PROPERTY_TYPE_0 = 5
GNU_PROPERTY_X86_ISA_1_NEEDED = 0xC0008002
GNU_PROPERTY_X86_ISA_1_USED = 0xC0010002

# Bit i of the ISA_1 bitmask is level i: baseline, v2, v3, v4.
LEVELS = ["baseline", "v2", "v3", "v4"]


def gnu_properties(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise SystemExit(f"{path}: not a little-endian ELF64 file")
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    props = {}
    for i in range(phnum):
        p_type, _, p_offset, _, _, p_filesz, _, p_align = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + i * phentsize)
        if p_type not in (PT_NOTE, PT_GNU_PROPERTY):
            continue
        off, end = p_offset, p_offset + p_filesz
        while off + 12 <= end:
            namesz, descsz, ntype = struct.unpack_from("<III", data, off)
            name = data[off + 12:off + 12 + namesz]
            desc = off + 12 + ((namesz + 3) & ~3)
            if name == b"GNU\x00" and ntype == NT_GNU_PROPERTY_TYPE_0:
                q = desc
                while q + 8 <= desc + descsz:
                    pr_type, pr_datasz = struct.unpack_from("<II", data, q)
                    if pr_datasz >= 4:
                        props[pr_type] = struct.unpack_from("<I", data, q + 8)[0]
                    q += 8 + ((pr_datasz + 7) & ~7)
            # Notes are padded to the segment's alignment: 8 for the
            # property note, 4 for the classic ones.
            align = 8 if p_align == 8 else 4
            off = desc + ((descsz + align - 1) & ~(align - 1))
    return props


def highest_level(mask):
    if not mask:
        return None
    return max(i for i in range(len(LEVELS)) if mask >> i & 1)


def describe(mask):
    if mask is None:
        return "absent"
    names = [LEVELS[i] for i in range(len(LEVELS)) if mask >> i & 1]
    return f"{mask:#x} ({', '.join(names) or 'none'})"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("binary")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--max", choices=LEVELS)
    group.add_argument("--exact", choices=LEVELS)
    args = parser.parse_args()

    props = gnu_properties(args.binary)
    needed = props.get(GNU_PROPERTY_X86_ISA_1_NEEDED)
    used = props.get(GNU_PROPERTY_X86_ISA_1_USED)
    print(f"{args.binary}")
    print(f"  ISA_1_NEEDED: {describe(needed)}")
    print(f"  ISA_1_USED:   {describe(used)}")

    level = highest_level(needed)
    if args.max is not None:
        limit = LEVELS.index(args.max)
        if level is not None and level > limit:
            print(f"error: needs x86-64-{LEVELS[level]}, expected at most "
                  f"x86-64-{args.max}", file=sys.stderr)
            return 1
    else:
        want = LEVELS.index(args.exact)
        if level != want:
            got = "no level" if level is None else f"x86-64-{LEVELS[level]}"
            print(f"error: needs {got}, expected exactly x86-64-{args.exact}",
                  file=sys.stderr)
            return 1
    print("  ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
