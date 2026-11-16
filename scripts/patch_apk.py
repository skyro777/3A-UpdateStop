#!/usr/bin/env python3
"""
3A Cloud Gaming (com.sayx.sagame 2.7.32) update-stopper patcher.

Goal: remove the force-update popup and permanently disable update checking,
while keeping every other byte of the APK identical.

Strategy (surgical, same-length byte replacements only - no apktool rebuild):
  1. AndroidManifest.xml (binary AXML)
       - versionName "2.7.32"  -> "9.9.99"   (same byte length in string pool)
       - versionCode  160749   -> 999999999  (4-byte int attribute data)
     => Anything reading the version through PackageManager (Java getVersion(),
        Flutter package_info) now believes the app is far newer than any
        published release, so "server version > local version" is never true.
  2. lib/arm64-v8a/libapp.so (Flutter AOT snapshot, Dart string pool)
       - "160749" -> "999999"  (standalone OneByteString, length-prefixed 0x8c)
       - "2.7.32" -> "9.9.99"  (standalone OneByteString, length-prefixed 0x8c)
       - "version_manage/get_info" -> "version_manage/get_inf0" (breaks the
         version-check HTTP endpoint; request errors are handled like the
         offline case and no popup is shown)
  3. classes*.dex (all 10 scanned, only classes8.dex expected to match)
       - hotfix polling URL
         "https://api-3acg.3ayx.net/3agame/version_manage/get_hotfix_info"
         -> same URL with trailing 'o' changed to '0' (disables the native
         hot-patch engine that could otherwise replace our patched libapp.so
         from the server on every launch)
       - "2.7.32" -> "9.9.99" (BuildConfig.VERSION_NAME string constant)
       - DEX SHA-1 signature + adler32 checksum recomputed after patching.
  4. Repack the ZIP preserving per-entry compression methods; STORED entries
     re-aligned exactly like the original (.so -> 16 KiB pages, others -> 4).
     All other entries are copied raw, byte-identical.

The output APK is unsigned; sign it afterwards (zipalign is NOT required
because alignment is applied during repack):
    apksigner sign --ks <keystore> --out patched-signed.apk patched-unsigned.apk

Usage:
    python3 patch_apk.py <input.apk> <output.apk>
"""

import hashlib
import struct
import sys
import zipfile
import zlib

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

ORIG_VERSION_NAME = "2.7.32"
ORIG_VERSION_CODE = 160749          # 0x273ed
NEW_VERSION_NAME = "9.9.99"         # same length as ORIG_VERSION_NAME
NEW_VERSION_CODE = 999999999        # 0x3B9AC9FF, fits int32

LIBAPP_FAKE_STRING = ("160749", "999999")   # standalone Dart OneByteStrings
LIBAPP_FAKE_NAME = ("2.7.32", "9.9.99")

CHECK_URL_FRAGMENT = ("version_manage/get_info", "version_manage/get_inf0")
HOTFIX_URL = (
    "https://api-3acg.3ayx.net/3agame/version_manage/get_hotfix_info",
    "https://api-3acg.3ayx.net/3agame/version_manage/get_hotfix_inf0",
)

SO_PAGE_ALIGN = 16384               # original APK aligns .so to 16 KiB
DEFAULT_ALIGN = 4


class PatchError(Exception):
    pass


def log(msg):
    print(f"[patch] {msg}")


# --------------------------------------------------------------------------
# binary AXML (AndroidManifest.xml) patcher
# --------------------------------------------------------------------------

def parse_axml_string_pool(data):
    """Return (strings, spans) where spans[i] = (start, end) byte range of the
    UTF-16LE character data for pool index i."""
    if struct.unpack_from("<I", data, 0)[0] != 0x00080003:
        raise PatchError("not a binary AXML file")
    typ, hdr, size = struct.unpack_from("<HHI", data, 8)
    if typ != 0x0001:
        raise PatchError(f"expected string pool chunk, got 0x{typ:04x}")
    sc, stylec, flags, str_start, _styles_start = struct.unpack_from("<IIIII", data, 8 + 8)
    if flags & (1 << 8):
        raise PatchError("UTF-8 string pool not expected for this manifest")
    strings, spans = [], []
    for i in range(sc):
        off = struct.unpack_from("<I", data, 8 + 28 + 4 * i)[0]
        p = 8 + str_start + off
        ln = struct.unpack_from("<H", data, p)[0]
        strings.append(data[p + 2:p + 2 + ln * 2].decode("utf-16-le"))
        spans.append((p + 2, p + 2 + ln * 2))
    return strings, spans


def patch_manifest(data):
    data = bytearray(data)
    strings, spans = parse_axml_string_pool(data)

    vername_idx = [i for i, s in enumerate(strings) if s == ORIG_VERSION_NAME]
    if len(vername_idx) != 1:
        raise PatchError(f"expected exactly one '{ORIG_VERSION_NAME}' pool string, found {len(vername_idx)}")

    # patch the versionName pool string bytes (same length)
    start, end = spans[vername_idx[0]]
    old_utf16 = ORIG_VERSION_NAME.encode("utf-16-le")
    new_utf16 = NEW_VERSION_NAME.encode("utf-16-le")
    if len(old_utf16) != len(new_utf16):
        raise PatchError("versionName replacement length mismatch")
    if bytes(data[start:end]) != old_utf16:
        raise PatchError("versionName pool bytes mismatch")
    data[start:end] = new_utf16
    log(f"manifest: versionName '{ORIG_VERSION_NAME}' -> '{NEW_VERSION_NAME}' at 0x{start:x}")

    # walk chunks, find <manifest> and its versionCode attribute
    pos = 8
    patched_code = False
    while pos + 8 <= len(data):
        typ, hdr, size = struct.unpack_from("<HHI", data, pos)
        if size == 0:
            raise PatchError(f"zero-sized chunk at 0x{pos:x}")
        if typ == 0x0102:  # RES_XML_START_ELEMENT_TYPE
            name = struct.unpack_from("<I", data, pos + 20)[0]
            if name < len(strings) and strings[name] == "manifest":
                attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, pos + 24)
                base = pos + 16 + attr_start  # attrExt begins at chunk+16
                for a in range(attr_count):
                    a_off = base + a * attr_size
                    a_name = struct.unpack_from("<I", data, a_off + 4)[0]
                    a_name_str = strings[a_name] if a_name < len(strings) else ""
                    dtype = data[a_off + 15]
                    if a_name_str == "versionCode" and dtype == 0x10:  # INT_DEC
                        cur = struct.unpack_from("<I", data, a_off + 16)[0]
                        if cur != ORIG_VERSION_CODE:
                            raise PatchError(f"versionCode attr is {cur}, expected {ORIG_VERSION_CODE}")
                        struct.pack_into("<I", data, a_off + 16, NEW_VERSION_CODE)
                        log(f"manifest: versionCode {ORIG_VERSION_CODE} -> {NEW_VERSION_CODE} at 0x{a_off + 16:x}")
                        patched_code = True
                break
        pos += size
    if not patched_code:
        raise PatchError("versionCode attribute not patched")
    return bytes(data)


# --------------------------------------------------------------------------
# libapp.so (Dart AOT snapshot) patcher
# --------------------------------------------------------------------------

def patch_libapp(data):
    data = bytearray(data)

    def replace_standalone(old, new):
        """Replace a standalone Dart OneByteString. The snapshot pool stores a
        len-prefix byte (len<<1)|0x80 immediately before the character data of
        short ASCII strings."""
        assert len(old) == len(new) and len(old) < 64
        prefix = ((len(old) << 1) | 0x80) & 0xFF
        needle = bytes([prefix]) + old.encode("ascii")
        hits, start = [], 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            hits.append(i + 1)
            start = i + 1
        if len(hits) != 1:
            raise PatchError(
                f"libapp.so: expected 1 standalone '{old}', found {len(hits)} "
                f"at {[hex(h) for h in hits]}")
        off = hits[0]
        data[off:off + len(old)] = new.encode("ascii")
        log(f"libapp.so: '{old}' -> '{new}' at 0x{off:x} (prefix 0x{prefix:02x})")

    replace_standalone(*LIBAPP_FAKE_STRING)
    replace_standalone(*LIBAPP_FAKE_NAME)

    # neutralise the version-check endpoint in every URL form present
    old_frag, new_frag = CHECK_URL_FRAGMENT
    needle = old_frag.encode()
    n, start = 0, 0
    while True:
        i = data.find(needle, start)
        if i < 0:
            break
        ctx = bytes(data[max(0, i - 44):i]).decode("ascii", "replace")
        data[i:i + len(needle)] = new_frag.encode()
        log(f"libapp.so: '{old_frag}' -> '{new_frag}' at 0x{i:x} | context: ...{ctx}")
        n += 1
        start = i + len(needle)
    if n == 0:
        raise PatchError("libapp.so: version-check URL fragment not found")
    log(f"libapp.so: broke {n} occurrence(s) of the version-check URL")

    if HOTFIX_URL[0].encode() in data:
        raise PatchError("libapp.so unexpectedly contains the hotfix URL")
    return bytes(data)


# --------------------------------------------------------------------------
# DEX patcher
# --------------------------------------------------------------------------

def fix_dex_checksums(data):
    """Recompute SHA-1 signature (offset 12..31) and adler32 (offset 8..11)."""
    data = bytearray(data)
    if bytes(data[:4]) != b"dex\n":
        raise PatchError("not a DEX file")
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xFFFFFFFF)
    return bytes(data)


def patch_dex(data, dex_name):
    data = bytearray(data)
    changed = False

    def swap_all(needle, repl, what):
        nonlocal changed
        start = 0
        while True:
            i = data.find(needle, start)
            if i < 0:
                break
            data[i:i + len(needle)] = repl
            log(f"{dex_name}: {what} at 0x{i:x}")
            changed = True
            start = i + len(needle)

    # break hotfix polling URL (same length)
    swap_all(HOTFIX_URL[0].encode(), HOTFIX_URL[1].encode(), "hotfix URL broken")

    # break version-check URL if it ever appears on the DEX side too
    swap_all(CHECK_URL_FRAGMENT[0].encode(), CHECK_URL_FRAGMENT[1].encode(),
             "version-check URL broken")

    # patch standalone "2.7.32" DEX string constants (uleb128 len prefix 0x06
    # for 6 ASCII chars, NUL-terminated MUTF-8)
    old_s, new_s = LIBAPP_FAKE_NAME
    swap_all(b"\x06" + old_s.encode() + b"\x00",
             b"\x06" + new_s.encode() + b"\x00",
             f"string '{old_s}' -> '{new_s}'")

    if changed:
        return fix_dex_checksums(data), True
    return bytes(data), False


# --------------------------------------------------------------------------
# ZIP repack (manual, preserves compression + replicates AGP alignment)
# --------------------------------------------------------------------------

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
                              0x04034b50, 20, 0, method, dos_time, dos_date,
                              crc, csize, usize, len(name), len(extra)))
        out.write(name)
        out.write(extra)
        out.write(payload)
        central.append((info, name, extra, crc, csize, usize, method,
                        dos_time, dos_date, out.tell() - 30 - len(name) - len(extra) - len(payload)))

    cd_start = out.tell()
    for info, name, extra, crc, csize, usize, method, dos_time, dos_date, new_off in central:
        out.write(struct.pack("<IHHHHHHIIIHHHHHII",
                              0x02014b50,
                              (info.create_system << 8) | 20, 20, 0,
                              method, dos_time, dos_date,
                              crc, csize, usize,
                              len(name), len(extra), 0, 0, 0,
                              info.external_attr, new_off))
        out.write(name)
        out.write(extra)
    cd_size = out.tell() - cd_start
    out.write(struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, len(central),
                          len(central), cd_size, cd_start, 0))
    out.close()
    fsrc.close()
    zin.close()
    log(f"repacked {len(central)} entries -> {dst_path}")


def verify_output(src_path, dst_path):
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
    log(f"content diffs ({len(diffs)}): {diffs}")
    return diffs


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    src, dst = sys.argv[1], sys.argv[2]
    z = zipfile.ZipFile(src)

    log("=== stage 1: AndroidManifest.xml ===")
    new_manifest = patch_manifest(z.read("AndroidManifest.xml"))

    log("=== stage 2: lib/arm64-v8a/libapp.so ===")
    new_libapp = patch_libapp(z.read("lib/arm64-v8a/libapp.so"))

    log("=== stage 3: DEX files ===")
    dex_replacements = {}
    for i in range(1, 11):
        name = "classes.dex" if i == 1 else f"classes{i}.dex"
        try:
            raw = z.read(name)
        except KeyError:
            continue
        patched, changed = patch_dex(raw, name)
        if changed:
            dex_replacements[name] = patched
    if "classes8.dex" not in dex_replacements:
        raise PatchError("classes8.dex was expected to require patching")

    replacements = {
        "AndroidManifest.xml": new_manifest,
        "lib/arm64-v8a/libapp.so": new_libapp,
    }
    replacements.update(dex_replacements)

    log("=== stage 4: repack ===")
    repack_apk(src, dst, replacements)

    log("=== stage 5: verify ===")
    diffs = verify_output(src, dst)
    expected = set(replacements)
    unexpected = set(diffs) - expected
    if unexpected:
        raise PatchError(f"unexpected content changes: {unexpected}")
    missing = expected - set(diffs)
    if missing:
        raise PatchError(f"expected changes missing: {missing}")

    # final assertions on the patched file
    zz = zipfile.ZipFile(dst)
    m = zz.read("AndroidManifest.xml")
    if NEW_VERSION_NAME.encode("utf-16-le") not in m:
        raise PatchError("patched manifest missing new versionName")
    la = zz.read("lib/arm64-v8a/libapp.so")
    for needle in (b"999999", b"9.9.99", b"version_manage/get_inf0"):
        if needle not in la:
            raise PatchError(f"libapp.so missing {needle!r}")
    if b"version_manage/get_info" in la or b"160749" in la or b"2.7.32" in la:
        raise PatchError("libapp.so still contains original version artifacts")
    for name, _ in dex_replacements.items():
        if b"get_hotfix_inf0" not in zz.read(name):
            raise PatchError(f"{name} missing broken hotfix URL")

    # verify alignment of stored entries
    with open(dst, "rb") as f:
        zc = zipfile.ZipFile(dst)
        for info in zc.infolist():
            if info.compress_type != 0 or info.is_dir():
                continue
            doff = local_data_offset(f, info)
            is_so = info.filename.startswith("lib/") and info.filename.endswith(".so")
            want = SO_PAGE_ALIGN if is_so else DEFAULT_ALIGN
            if doff % want != 0:
                raise PatchError(f"{info.filename}: data offset 0x{doff:x} not {want}-aligned")

    h = hashlib.sha256(open(dst, "rb").read()).hexdigest()
    with open(dst + ".sha256", "w") as f:
        f.write(h + "  " + dst.split("/")[-1] + "\n")
    log(f"SUCCESS: patched (unsigned) APK written to {dst}")
    log(f"sha256: {h}")


if __name__ == "__main__":
    main()
