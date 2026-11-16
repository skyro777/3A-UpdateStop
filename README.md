# 3A-UpdateStop

Keeping the preferred old version of **3A Cloud Gaming (3A云游)** alive: the
force-update popup after login is removed and update checking is permanently
disabled.

- Original APK: `3A CLOUD GAMING UPDATE.apk` (Git LFS) — `com.sayx.sagame`
  **2.7.32 (160749)**, Flutter (Dart 3.6.0), 10 DEX, 49 native libs
- Patched APK: see release **[v2.7.32-noupdate.2](https://github.com/skyro777/3A-UpdateStop/releases/tag/v2.7.32-noupdate.2)**

> The first release (`v2.7.32-noupdate`) **crashed on launch** and has been
> deleted. Details in the post-mortem below.

## Download

| File | What |
|---|---|
| [`3A-CLOUD-GAMING-2.7.32-NOUPDATE.apk`](https://github.com/skyro777/3A-UpdateStop/releases/download/v2.7.32-noupdate.2/3A-CLOUD-GAMING-2.7.32-NOUPDATE.apk) | Patched, signed, ready to install |
| `release-sha256.txt` | published alongside the APK |

## Install

1. **Uninstall the previous build first** (the original app *and* the broken
   first patched build, if installed). This build is re-signed (the original
   signing key is not available), so Android refuses in-place updates, and a
   stale hot-patch cache from an old install would also be a problem. A clean
   install also clears any cached update-check response.
2. Install the APK, allow installs from unknown sources.
3. Log in. The update popup that used to appear right after login
   (“发现新版本 V2.7.36 / 抢先体验”) must no longer appear.

## What is patched (2 ZIP entries out of 6,952)

| File | Change | Why |
|---|---|---|
| `lib/arm64-v8a/libapp.so` | `version_manage/get_info` → `version_manage/get_inf0` | This is the **only data source** of the post-login update popup. The Dart code (`game_3a/dialog/upgrade/upgrade_dialog.dart`) shows the dialog only when the `POST /3agame/version_manage/get_info` response says a newer version exists. With the endpoint broken the request fails exactly like the offline case (the server returns a clean 404), and the dialog is never triggered. |
| `classes8.dex` | `version_manage/get_hotfix_info` → `...get_hotfix_inf0` | The app ships a native hot-patch engine (`nc.*` / `com.hotpatch.SoPatchLoader`, initialised in `CloudGame.attachBaseContext`, polled right after first login) that can bsdiff-replace `libapp.so` from the server on every launch. With this URL broken it can never revert the patch above. |

**Deliberately NOT patched anymore** (v1 did, and it caused the crash — and
the spoofing was pointless anyway): the version values. The manifest keeps
`versionName 2.7.32` / `versionCode 160749`, the Dart constants and
`BuildConfig.VERSION_NAME` keep their original values. The app reports its
real version to 3A's servers exactly like the genuine build — this is the
safest option for login compatibility (`login_by_wx` and every other API
request carries the version from `PackageInfo`).

Everything else in the APK is byte-identical to the original, including all
49 native libraries (kept 16 KiB page-aligned, `extractNativeLibs=false`
preserved), all resources and all other DEX files.

## Post-mortem: why the first patched build crashed

The first patch also rewrote the string `"2.7.32"` → `"9.9.99"` inside
`classes8.dex` (`BuildConfig.VERSION_NAME`) using a blind byte search. DEX
string tables must be **sorted by UTF-16 code point order**; `'9'` (U+0039)
sorts after `'2'` (U+0032), so the patched string jumped past its neighbour
`"200"` in the sorted table:

```
W com.sayx.sagam: Zip open failed: Failure to verify dex file
  '.../base.apk!classes8.dex': Out-of-order string_ids: '9.9.99' then '200'
E AndroidRuntime: java.lang.ClassNotFoundException:
  Didn't find class "com.sayx.sagame.CloudGame"
```

Android's `DexFileVerifier` rejected the whole `classes8.dex` (which contains
the `CloudGame` Application class), so the process died on every launch.

Fixes in v2 of the patcher (`scripts/patch_apk.py`):

- the DEX patch is applied only after **parsing the real `string_ids`
  table** and locating the target string by pool index (no blind search);
- the replacement is proven to preserve the sorted order against **both
  neighbours** before a single byte is written;
- after patching, the **entire string table (59,838 entries)** is re-parsed
  and re-validated;
- the CI pipeline runs **`dexdump`** (the same ART `libdexfile` verifier the
  device uses) over every DEX file and aborts the release if any of them
  fails to verify.

## WeChat login

The WeChat login flow itself is **not touched** by the patch (fluwx plugin,
`com.sayx.sagame.wxapi.WXEntryActivity`, app id `wx6c9838146fd2cc29` — all
byte-identical to the original). However there is one honest caveat:

- The WeChat client (`com.tencent.mm`) verifies that the **calling APK's
  signature** matches the certificate registered by the developer on WeChat
  Open Platform for the app id. This build is necessarily re-signed (the
  original key is not available), so WeChat login **may return errCode -1**
  (“应用签名不一致”).
- **Test it** — open the app → WeChat login. If WeChat opens and returns to
  the app with a success toast, everything is fine.
- If it fails: the check happens inside the WeChat app's process (it reads
  the signature from the system `PackageManager`), so **no patch inside the
  3A APK can influence it** — this is true for every re-signed APK of every
  app, not just this one. Realistic options in that case:
  1. **Root + signature-spoofing module** (cleanest): on a rooted device
     (e.g. Magisk + LSPosed on your Android 10 OnePlus), a signature-spoof
     module can make WeChat see the original 3A certificate
     (SHA-1 `f5a5d7f20191310e6b268937bf88847b9c674525`) for
     `com.sayx.sagame`. The original certificate is available in this
     repository's original APK, so the exact value to spoof is known.
  2. **Bind a phone number to the account first** (in the *original* app,
     before switching to the patched build), then use phone-number login on
     the patched build — same account, same games, same data.
- WeChat **Pay** inside the app has the same signature dependency and the
  same caveat. In-app top-ups that go through the server-side order flow
  (pay URLs from `OrderForWxChatDetailInfo`) are affected the same way.

### How the update flow works (analysis findings)

- The version check is a `POST https://api-3acg.3ayx.net/3agame/version_manage/get_info`
  with `{"platform_type": 1}` (found via blutter string analysis + confirmed
  by probing the endpoint).
- The server **always** returns the latest version info regardless of the
  client's reported version (`version: "2.7.36_160783"`, `is_mandatory`,
  `title: "发现新版本"`, `contents`, APK `url`) — the comparison is done
  client-side in Dart.
- The popup is `UpgradeDialog` with buttons `抢先体验` / `立即安装` / `下次再说`
  (`下次再说` is hidden when the server marks the update mandatory).
- A separate, harmless `version_update` message type exists in the in-app
  message center (list item, not a modal).
- The native hot-patch engine polls
  `POST .../version_manage/get_hotfix_info` with
  `{"versionTag": "2.7.32_160749", "userId": ...}` right after the first
  login and can replace `libapp.so` from `filesDir/app_patched/` — hence the
  second patch.

## Reproducing

Workflows in `.github/workflows/` (all heavy work runs in GitHub Actions, not
locally):

| Workflow | Purpose |
|---|---|
| `apk-reconnaissance.yml` | APK metadata/structure inventory |
| `3a-cloud-gaming-update-full-decompile.yml` | JADX Java + apktool smali/resources + native inventory |
| `blutter-analysis.yml` | String-level analysis of the Flutter AOT payload (`libapp.so`) |
| `patch-and-release.yml` | Runs `scripts/patch_apk.py` (v2) on the original LFS APK, signs with `ci/3amod.keystore`, verifies (apksigner / zipalign / aapt / **dexdump on every DEX** / byte-diff) and publishes the GitHub release |

The patcher (`scripts/patch_apk.py`) is fully deterministic — the release APK
is bit-for-bit reproducible from this repository.

```bash
# local reproduction
python3 scripts/patch_apk.py "3A CLOUD GAMING UPDATE.apk" patched-unsigned.apk
apksigner sign --ks ci/3amod.keystore --ks-pass pass:3AUpdateStop!2025 \
  --ks-key-alias 3amod --out patched-signed.apk patched-unsigned.apk
# structural verification with ART's own verifier (same as on-device)
dexdump -l plain patched-unsigned.apk  # or extract classes*.dex first
```

## Files

```
3A CLOUD GAMING UPDATE.apk   original APK (Git LFS, sha256 e06059bf...955cb)
Screenshot_20261116-113510.jpg  the original update popup
log.txt                       adb logcat of the v1 crash (out-of-order string_ids)
scripts/patch_apk.py          the surgical patcher (v2, sort-safe)
scripts/apk_diff_report.py    independent CI verification
ci/3amod.keystore             release signing key (throwaway, for this mod only)
```
