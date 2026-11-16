# 3A-UpdateStop

Keeping the preferred old version of **3A Cloud Gaming (3A云游)** alive: the
force-update popup after login is removed and update checking is permanently
disabled.

- Original APK: `3A CLOUD GAMING UPDATE.apk` (Git LFS) — `com.sayx.sagame`
  **2.7.32 (160749)**, Flutter (Dart 3.6.0), 10 DEX, 49 native libs
- Patched APK: see release **[v2.7.32-noupdate](https://github.com/skyro777/3A-UpdateStop/releases/tag/v2.7.32-noupdate)**

## Download

| File | What |
|---|---|
| [`3A-CLOUD-GAMING-2.7.32-NOUPDATE.apk`](https://github.com/skyro777/3A-UpdateStop/releases/download/v2.7.32-noupdate/3A-CLOUD-GAMING-2.7.32-NOUPDATE.apk) | Patched, signed, ready to install |
| `release-sha256.txt` | `09d4ef2ff282943c8d06862b346a1e1a16f4feb87197cb1e1e61b6c4f4e23aac` |

## Install

1. **Uninstall the original app first.** This build is re-signed (the original
   signing key is not available), so Android refuses in-place updates, and a
   stale hot-patch cache from the old install would also be a problem.
2. **WeChat login may no longer work** (re-signed APKs usually get `errCode -1`
   from the WeChat client because it validates the signature registered by the
   developer on WeChat Open Platform). If you log in via WeChat: **bind a
   mobile number to your account in the old app BEFORE uninstalling**, then
   log in on this build with phone number + SMS code — same account, same
   games, same data.
3. Install the APK, allow installs from unknown sources.

## What was patched (3 ZIP entries out of 6,952)

| File | Change | Why |
|---|---|---|
| `AndroidManifest.xml` | `versionName` `2.7.32` → `9.9.99`, `versionCode` `160749` → `999999999` (same-length bytes) | The Java `getVersion()` helper (`CloudGame.smali`) and the `package_info` Flutter plugin both read the version from `PackageManager` — everything now sees a version far newer than any release, so every `server > local` comparison is false |
| `lib/arm64-v8a/libapp.so` | Dart constants `"160749"` → `"999999"`, `"2.7.32"` → `"9.9.99"` (standalone pool strings, length-prefix `0x8c`); `version_manage/get_info` → `version_manage/get_inf0` | Same version-faking for the hardcoded Dart constants; **and the version-check API endpoint is disabled outright** — the request 404s, the Dart error path is treated like "offline", and the `UpgradeDialog` (`game_3a/dialog/upgrade/upgrade_dialog.dart`) is never triggered |
| `classes8.dex` | `version_manage/get_hotfix_info` → `...get_hotfix_inf0`; `BuildConfig.VERSION_NAME` `"2.7.32"` → `"9.9.99"`; DEX SHA-1 + adler32 recomputed | The app ships a native hot-patch engine (`nc.*` classes, initialized in `CloudGame.attachBaseContext`) that can bsdiff-replace `libapp.so` from the server on every launch — with the URL broken it can never undo these patches |

Everything else in the APK is byte-identical to the original, including all
49 native libraries (kept 16 KiB page-aligned, `extractNativeLibs=false`
preserved), all resources and all other DEX files.

### How the update flow works (analysis findings)

- The version check is a `POST https://api-3acg.3ayx.net/3agame/version_manage/get_info`
  with `{"platform_type": 1}` (found via blutter + confirmed by probing the endpoint).
- The server **always** returns the latest version info regardless of the
  client's reported version (`version: "2.7.36_160783"`, `is_mandatory`,
  `title: "发现新版本"`, `contents`, APK `url`) — the comparison is done
  client-side in Dart.
- The popup is `UpgradeDialog` with buttons `抢先体验` / `立即安装` / `下次再说`
  (`下次再说` is hidden when the server marks the update mandatory).
- A separate, harmless `version_update` message type exists in the in-app
  message center (list item, not a modal).

## Reproducing

Workflows in `.github/workflows/` (all heavy work runs in GitHub Actions, not
locally):

| Workflow | Purpose |
|---|---|
| `apk-reconnaissance.yml` | APK metadata/structure inventory |
| `3a-cloud-gaming-update-full-decompile.yml` | JADX Java + apktool smali/resources + native inventory |
| `blutter-analysis.yml` | Reverses the Flutter AOT payload (`libapp.so`) with [blutter](https://github.com/worawit/blutter) to locate the Dart update logic |
| `patch-and-release.yml` | Runs `scripts/patch_apk.py` on the original LFS APK, signs with `ci/3amod.keystore`, verifies (apksigner / zipalign / aapt / byte-diff) and publishes the GitHub release |

The patcher (`scripts/patch_apk.py`) is fully deterministic — the release APK
is bit-for-bit reproducible from this repository.

```bash
# local reproduction
python3 scripts/patch_apk.py "3A CLOUD GAMING UPDATE.apk" patched-unsigned.apk
apksigner sign --ks ci/3amod.keystore --ks-pass pass:3AUpdateStop!2025 \
  --ks-key-alias 3amod --out patched-signed.apk patched-unsigned.apk
```

## Files

```
3A CLOUD GAMING UPDATE.apk   original APK (Git LFS, sha256 e06059bf...955cb)
Screenshot_20261116-113510.jpg  the original update popup
scripts/patch_apk.py          the surgical patcher
scripts/apk_diff_report.py    independent CI verification
ci/3amod.keystore             release signing key (throwaway, for this mod only)
```
