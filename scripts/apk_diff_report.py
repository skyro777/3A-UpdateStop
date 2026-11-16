#!/usr/bin/env python3
"""
Independent CI verification: byte-level diff report between the original and
the patched APK. Prints per-entry SHA-256 changes and a summary line
"changed_entries=N". Exits non-zero on structural problems (entry list
change, compression method change, unexpected entry count).
"""

import hashlib
import re
import sys
import zipfile

SIG_RE = re.compile(
    r"^META-INF/(MANIFEST\.MF|[^/]+\.(SF|RSA|DSA|EC))$", re.IGNORECASE)


def entry_hashes(path):
    z = zipfile.ZipFile(path)
    out = {}
    for info in z.infolist():
        if info.is_dir():
            continue
        if SIG_RE.match(info.filename):
            continue  # v1 signature files legitimately differ after re-signing
        out[info.filename] = (
            info.compress_type,
            hashlib.sha256(z.read(info.filename)).hexdigest()[:16],
        )
    return out


def main():
    if len(sys.argv) != 3:
        print("usage: apk_diff_report.py <original.apk> <patched.apk>")
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]
    a, b = entry_hashes(src), entry_hashes(dst)

    if set(a) != set(b):
        print(f"[diff] FATAL: entry name sets differ "
              f"(only-src={sorted(set(a)-set(b))[:5]} only-dst={sorted(set(b)-set(a))[:5]})")
        sys.exit(1)
    if len(a) != 6949:
        print(f"[diff] FATAL: expected 6949 non-signature entries, found {len(a)}")
        sys.exit(1)

    changed = []
    for name in a:
        if a[name] != b[name]:
            if a[name][0] != b[name][0]:
                print(f"[diff] FATAL: compression method changed for {name}")
                sys.exit(1)
            changed.append((name, a[name][1], b[name][1]))
    for name, old, new in sorted(changed):
        print(f"[diff] CHANGED {name}: sha256 {old} -> {new}")

    print(f"[diff] compared {len(a)} entries, changed_entries={len(changed)}")
    for name, _, _ in changed:
        print(f"[diff] changed: {name}")


if __name__ == "__main__":
    main()
