#!/usr/bin/env python3
"""
3A Cloud Gaming (com.sayx.sagame 2.7.32) update-stopper patcher - v2.

v1 post-mortem (why the first release crashed on Android 10):
  v1 replaced the string "2.7.32" -> "9.9.99" inside classes8.dex with a blind
  byte search. The DEX spec requires string_ids to be sorted by UTF-16 code
  point order; '9' (U+0039) sorts AFTER '2' (U+0032), so the patched string
  jumped past its neighbour "200" in the sorted table. Android's
  DexFileVerifier rejected the WHOLE classes8.dex ("Out-of-order string_ids"),
  the Application class com.sayx.sagame.CloudGame could no longer be loaded,
  and the app crashed on launch with ClassNotFoundException (black screen ~2s
  then crash, every time).

v2 strategy - minimal, surgical, and LOGIN-SAFE:
  Version spoofing is removed entirely. The app sends its real version to
  3A's servers in API request params (app_version / version_name /
  version_code, read from PackageInfo - incl. the login_by_wx request), so a
  fake version could break login or trigger server-side unknown-version
  handling. Instead the popup is eliminated at its only data source:

  1. lib/arm64-v8a/libapp.so  (Flutter AOT snapshot)
       "version_manage/get_info" -> "version_manage/get_inf0"
     The one and only version-check endpoint (POST
     https://api-3acg.3ayx.net/3agame/version_manage/get_info, the response
     of which feeds the UpgradeDialog "发现新版本 / 抢先体验" popup right after
     login). The broken path returns a clean HTTP 404 text/plain, which the
     Dart error path treats exactly like "no network": no dialog.
  2. classes8.dex
       "https://api-3acg.3ayx.net/3agame/version_manage/get_hotfix_info"
       -> ".../get_hotfix_inf0"
     Disables the native hot-patch engine (nc.* / com.hotpatch.SoPatchLoader,
     initialised in CloudGame.attachBaseContext) that can bsdiff-replace
     libapp.so from the server on every launch - it must never be able to
     revert patch #1.
     The string is located by PARSING the real string_ids table (no blind
     byte search), the replacement is proven to preserve the table's sorted
     order (pre-check against both neighbours with the spec's UTF-16
     comparator), the DEX SHA-1 signature + adler32 checksum are recomputed,
     and the ENTIRE string table is re-parsed and re-validated afterwards.

  Everything else - AndroidManifest.xml (versionName 2.7.32, versionCode
  160749 both untouched), all other DEX files, all 49 native libraries, all
  resources - stays byte-identical to the original APK. To 3A's servers the
  app presents itself exactly like the genuine 2.7.32 build.

Output APK is unsigned. Sign afterwards (alignment is already applied during
repack, mirroring the original APK layout):
    apksigner sign --ks <keystore> --out patched-signed.apk patched-unsigned.apk

Usage:
    python3 patch_apk.py <input.apk> <output.apk>
"""

import hashlib
import struct
import sys
import zipfile
import zlib

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

LIBAPP_BREAK = (
    "version_manage/get_info",
    "version_manage/get_inf0",
)

DEX_HOTFIX_URL = (
    "https://api-3acg.3ayx.net/3agame/version_manage/get_hotfix_info",
    "https://api-3acg.3ayx.net/3agame/version_manage/get_hotfix_inf0",
)
HOTFIX_DEX = "classes8.dex"

SO_PAGE_ALIGN = 16384               # original APK aligns .so to 16 KiB pages
DEFAULT_ALIGN = 4

ORIGINAL_VERSION_NAME = "2.7.32"    # must remain untouched (login safety)
ORIGINAL_VERSION_CODE = 160749


class PatchError(Exception):
    pass


def log(msg):
    print(f"[patch] {msg}")


# ---------------------------------------------------------------------------
# DEX string table parsing / spec-order comparator
# ---------------------------------------------------------------------------

def read_uleb128(data, p):
    result = 0
    shift = 0
    while True:
        b = data[p]
        p += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if b < 0x80:
            break
    return result, p


def parse_dex_strings(data):
    """Parse a DEX file's string_ids table.

    Returns (strings, offsets) where strings[i] is the raw MUTF-8 character
    bytes of pool entry i (NUL terminator excluded) and offsets[i] is the file
    offset of the first character byte.
    """
    if bytes(data[:4]) != b"dex\n":
        raise PatchError("not a DEX file")
    string_ids_size, string_ids_off = struct.unpack_from("<II", data, 56)
    strings, offsets = [], []
    for i in range(string_ids_size):
        data_off = struct.unpack_from("<I", data, string_ids_off + 4 * i)[0]
        # string_data_item: uleb128 utf16_size, then mutf8 bytes, then NUL
        utf16_size, p = read_uleb128(data, data_off)
        start = p
        units = 0
        while units < utf16_size:
            a = data[p]
            if a == 0:
                raise PatchError(f"string {i}: premature NUL in MUTF-8 data")
            if a < 0x80:
                p += 1
            elif a < 0xE0:
                p += 2
            else:
                p += 3
            units += 1
        if data[p] != 0:
            raise PatchError(f"string {i}: missing NUL terminator")
        strings.append(bytes(data[start:p]))
        offsets.append(start)
    return strings, offsets


def mutf8_to_utf16(b):
    """Decode MUTF-8 bytes to a list of UTF-16 code units (no surrogate
    combining - MUTF-8 encodes supplementary chars as surrogate pairs)."""
    units = []
    i, n = 0, len(b)
    while i < n:
        a = b[i]
        if a < 0x80:
            units.append(a)
            i += 1
        elif a < 0xE0:
            units.append(((a & 0x1F) << 6) | (b[i + 1] & 0x3F))
            i += 2
        elif a < 0xF0:
            units.append(((a & 0x0F) << 12) | ((b[i + 1] & 0x3F) << 6)
                         | (b[i + 2] & 0x3F))
            i += 3
        else:
            raise PatchError("invalid MUTF-8 byte >= 0xF0")
    return units


def dex_str_cmp(a, b):
    """DEX spec ordering: compare as sequences of UTF-16 code unit values;
    a shorter string that is a prefix of a longer one sorts first."""
    ua, ub = mutf8_to_utf16(a), mutf8_to_utf16(b)
    if ua == ub:
        return 0
    return -1 if ua < ub else 1


def validate_string_table_sorted(strings, dex_name):
    """Full-table check: string_ids must be strictly ascending. This is the
    check Android's DexFileVerifier performs - it is what v1 violated."""
    for i in range(1, len(strings)):
        if dex_str_cmp(strings[i - 1], strings[i]) >= 0:
            raise PatchError(
                f"{dex_name}: OUT-OF-ORDER string_ids at index {i}: "
                f"{strings[i-1]!r} >= {strings[i]!r}")
    return True


def fix_dex_checksums(data):
    """Recompute SHA-1 signature (bytes 12..31) and adler32 (bytes 8..11)."""
    data = bytearray(data)
    if bytes(data[:4]) != b"dex\n":
        raise PatchError("not a DEX file")
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xFFFFFFFF)
    return bytes(data)


def verify_dex_checksums(data, dex_name):
    if bytes(data[:4]) != b"dex\n":
        raise PatchError(f"{dex_name}: not a DEX file")
    want_sha1 = hashlib.sha1(data[32:]).digest()
    if bytes(data[12:32]) != want_sha1:
        raise PatchError(f"{dex_name}: SHA-1 signature mismatch")
    want_adler = zlib.adler32(data[12:]) & 0xFFFFFFFF
    if struct.unpack_from("<I", data, 8)[0] != want_adler:
        raise PatchError(f"{dex_name}: adler32 checksum mismatch")
    return True


def patch_dex_string(data, dex_name, old, new):
    """Replace exactly one pool string `old` with same-encoded-length `new`,
    after proving the replacement keeps the string table sorted. Returns the
    patched bytes."""
    old_b, new_b = old.encode(), new.encode()
    if len(old_b) != len(new_b):
        raise PatchError(f"{dex_name}: replacement must keep encoded length")

    strings, offsets = parse_dex_strings(data)
    validate_string_table_sorted(strings, dex_name)
    try:
        idx = strings.index(old_b)
    except ValueError:
        raise PatchError(f"{dex_name}: string {old!r} not found in pool")
    if strings.count(old_b) != 1:
        raise PatchError(f"{dex_name}: string {old!r} not unique in pool")

    # sort-safety proof against both neighbours (the v1 bug!)
    if idx > 0:
        prev = strings[idx - 1]
        if dex_str_cmp(prev, new_b) >= 0:
            raise PatchError(
                f"{dex_name}: replacement {new!r} would not sort after "
                f"predecessor {prev!r}")
    if idx + 1 < len(strings):
        nxt = strings[idx + 1]
        if dex_str_cmp(new_b, nxt) >= 0:
            raise PatchError(
                f"{dex_name}: replacement {new!r} would not sort before "
                f"successor {nxt!r}")

    out = bytearray(data)
    off = offsets[idx]
    if bytes(out[off:off + len(old_b)]) != old_b:
        raise PatchError(f"{dex_name}: pool offset sanity check failed")
    out[off:off + len(new_b)] = new_b
    log(f"{dex_name}: pool string #{idx} {old!r} -> {new!r} @ 0x{off:x} "
        f"(prev={strings[idx-1]!r}, next={strings[idx+1]!r})"
        if len(strings) > idx + 1 else
        f"{dex_name}: pool string #{idx} {old!r} -> {new!r} @ 0x{off:x}")

    out = bytearray(fix_dex_checksums(out))

    # full re-parse and re-validation of the patched file
    strings2, offsets2 = parse_dex_strings(out)
    validate_string_table_sorted(strings2, dex_name)
    if strings2[idx] != new_b:
        raise PatchError(f"{dex_name}: post-patch string mismatch")
    verify_dex_checksums(out, dex_name)
    log(f"{dex_name}: re-parsed {len(strings2)} pool strings - sorted order "
        f"VALID, SHA-1 + adler32 recomputed and verified")
    return bytes(out)


# ---------------------------------------------------------------------------
# libapp.so (Dart AOT snapshot) patcher
# ---------------------------------------------------------------------------

def patch_libapp(data):
    data = bytearray(data)
    old, new = LIBAPP_BREAK
    needle, repl = old.encode(), new.encode()
    hits, start = [], 0
    while True:
        i = data.find(needle, start)
        if i < 0:
            break
        hits.append(i)
        start = i + 1
    if len(hits) != 1:
        raise PatchError(
            f"libapp.so: expected exactly 1 occurrence of {old!r}, "
            f"found {len(hits)} at {[hex(h) for h in hits]}")
    i = hits[0]
    ctx = bytes(data[max(0, i - 44):i]).decode("ascii", "replace")
    data[i:i + len(needle)] = repl
    log(f"libapp.so: {old!r} -> {new!r} at 0x{i:x} | context: ...{ctx}")
    # the version-check URL must now be unreachable from the Dart side
    if bytes(data).count(needle) != 0:
        raise PatchError("libapp.so: version-check fragment still present")
    return bytes(data)


# ---------------------------------------------------------------------------
# ZIP repack (preserves compression + replicates the original alignment)
# ---------------------------------------------------------------------------

def dos_datetime(dt):
    y, mo, d, h, mi, s = dt
    y = max(1980, min(2107, y))
    dos_date = ((y - 1980) << 9) | (mo << 5) | d
    dos_time = (h << 11) | (mi << 5) | (s // 2)
    return dos_time, dos_date


def local_data_offset(f, info):
    f.seek(info.header_offset)
    hdr = f.read(30)
    if hdr[:4] != b"PK\x03\x04":
        raise PatchError(f"bad local header for {info.filename}")
    nl, el = struct.unpack_from("<HH", hdr, 26)
    return info.header_offset + 30 + nl + el


def repack_apk(src_path, dst_path, replacements):
    zin = zipfile.ZipFile(src_path)
    fsrc = open(src_path, "rb")
    out = open(dst_path, "wb")
    central = []

    for info in zin.infolist():
        if info.is_dir():
            continue
        name = info.filename.encode("utf-8")
        method = info.compress_type

        if info.filename in replacements:
            raw = replacements[info.filename]
            if method == 0:
                payload = raw
                csize = usize = len(raw)
            else:
                co = zlib.compressobj(9, zlib.DEFLATED, -15)
                payload = co.compress(raw) + co.flush()
                csize, usize = len(payload), len(raw)
            crc = zlib.crc32(raw) & 0xFFFFFFFF
        else:
            fsrc.seek(local_data_offset(fsrc, info))
            payload = fsrc.read(info.compress_size)
            crc, csize, usize = info.CRC, info.compress_size, info.file_size

        dos_time, dos_date = dos_datetime(info.date_time)

        # alignment padding for STORED entries (mirrors the original layout)
        extra = b""
        if method == 0:
            is_so = info.filename.startswith("lib/") and info.filename.endswith(".so")
            align = SO_PAGE_ALIGN if is_so else DEFAULT_ALIGN
            cur = out.tell() + 30 + len(name)
            pad = (align - (cur % align)) % align
            extra = b"\x00" * pad

        out.write(struct.pack("<IHHHHHIIIHH",
                              0x04034B50, 20, 0, method, dos_time, dos_date,
                              crc, csize, usize, len(name), len(extra)))
        out.write(name)
        out.write(extra)
        out.write(payload)
        central.append((info, name, extra, crc, csize, usize, method,
                        dos_time, dos_date,
                        out.tell() - 30 - len(name) - len(extra) - len(payload)))

    cd_start = out.tell()
    for info, name, extra, crc, csize, usize, method, dos_time, dos_date, new_off in central:
        out.write(struct.pack("<IHHHHHHIIIHHHHHII",
                              0x02014B50,
                              (info.create_system << 8) | 20, 20, 0,
                              method, dos_time, dos_date,
                              crc, csize, usize,
                              len(name), len(extra), 0, 0, 0,
                              info.external_attr, new_off))
        out.write(name)
        out.write(extra)
    cd_size = out.tell() - cd_start
    out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central),
                          len(central), cd_size, cd_start, 0))
    out.close()
    fsrc.close()
    zin.close()
    log(f"repacked {len(central)} entries -> {dst_path}")


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_output(src_path, dst_path, expected_diffs):
    zsrc = zipfile.ZipFile(src_path)
    zdst = zipfile.ZipFile(dst_path)
    s_names = [i.filename for i in zsrc.infolist() if not i.is_dir()]
    d_names = [i.filename for i in zdst.infolist() if not i.is_dir()]
    if s_names != d_names:
        raise PatchError("entry name list changed")

    diffs = []
    for i in zdst.infolist():
        if i.is_dir():
            continue
        i_s = zsrc.getinfo(i.filename)
        if i.compress_type != i_s.compress_type:
            raise PatchError(f"{i.filename}: compression method changed")
        if zsrc.read(i.filename) != zdst.read(i.filename):
            diffs.append(i.filename)

    if set(diffs) != set(expected_diffs):
        raise PatchError(
            f"content diffs {sorted(diffs)} != expected {sorted(expected_diffs)}")
    log(f"content diffs exactly as expected ({len(diffs)}): {sorted(diffs)}")

    # manifest must be byte-identical: no version spoofing in v2 (login safety)
    if zdst.read("AndroidManifest.xml") != zsrc.read("AndroidManifest.xml"):
        raise PatchError("AndroidManifest.xml must be untouched in v2")
    m = zdst.read("AndroidManifest.xml")
    if ORIGINAL_VERSION_NAME.encode("utf-16-le") not in m:
        raise PatchError("manifest lost original versionName")

    # every dex except the patched one must be byte-identical AND valid
    for i in range(1, 11):
        name = "classes.dex" if i == 1 else f"classes{i}.dex"
        try:
            raw = zdst.read(name)
        except KeyError:
            continue
        verify_dex_checksums(raw, name)
        strings, _ = parse_dex_strings(raw)
        validate_string_table_sorted(strings, name)
        if name != HOTFIX_DEX and zsrc.read(name) != raw:
            raise PatchError(f"{name}: unexpected content change")
    log("all 10 DEX files: checksums valid, string tables sorted, "
        f"only {HOTFIX_DEX} modified")

    # libapp.so assertions
    la = zdst.read("lib/arm64-v8a/libapp.so")
    if LIBAPP_BREAK[1].encode() not in la:
        raise PatchError("libapp.so missing broken version-check URL")
    if LIBAPP_BREAK[0].encode() in la:
        raise PatchError("libapp.so still contains working version-check URL")

    # alignment of stored entries must mirror the original
    with open(dst_path, "rb") as f:
        zc = zipfile.ZipFile(dst_path)
        for info in zc.infolist():
            if info.compress_type != 0 or info.is_dir():
                continue
            doff = local_data_offset(f, info)
            is_so = info.filename.startswith("lib/") and info.filename.endswith(".so")
            want = SO_PAGE_ALIGN if is_so else DEFAULT_ALIGN
            if doff % want != 0:
                raise PatchError(
                    f"{info.filename}: data offset 0x{doff:x} not {want}-aligned")
    log("stored-entry alignment verified (.so 16 KiB pages, others 4-byte)")

    h = hashlib.sha256(open(dst_path, "rb").read()).hexdigest()
    with open(dst_path + ".sha256", "w") as f:
        f.write(h + "  " + dst_path.split("/")[-1] + "\n")
    log(f"SUCCESS: patched (unsigned) APK written to {dst_path}")
    log(f"sha256: {h}")


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]
    z = zipfile.ZipFile(src)

    log("=== v2 patch: minimal, sort-safe, no version spoofing ===")
    log(f"input: {src}")

    log("--- stage 1: lib/arm64-v8a/libapp.so (break version-check endpoint) ---")
    new_libapp = patch_libapp(z.read("lib/arm64-v8a/libapp.so"))

    log(f"--- stage 2: {HOTFIX_DEX} (break hot-patch endpoint, sort-safe) ---")
    new_dex = patch_dex_string(z.read(HOTFIX_DEX), HOTFIX_DEX,
                               DEX_HOTFIX_URL[0], DEX_HOTFIX_URL[1])

    log("--- stage 3: repack ---")
    replacements = {
        "lib/arm64-v8a/libapp.so": new_libapp,
        HOTFIX_DEX: new_dex,
    }
    repack_apk(src, dst, replacements)

    log("--- stage 4: verify ---")
    verify_output(src, dst, expected_diffs=list(replacements))


if __name__ == "__main__":
    main()
