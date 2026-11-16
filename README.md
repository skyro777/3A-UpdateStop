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

Verified on-device: the **微信登录 (WeChat SSO) button fails with errCode -6**
（微信授权失败:-6 - "Incorrect signature. Check that the signature is the
same as that on the open platform."). This is expected and unavoidable for
**any** re-signed APK: the WeChat client (com.tencent.mm) reads the calling
app's signing certificate through the system PackageManager and compares
its MD5 against the certificate registered on WeChat Open Platform for
app id `wx6c9838146fd2cc29` - which is 3ayx's own certificate
(MD5 `32a5bf5af1294ad89bf1d8d25306c64e`, `CN=Lucers Tu, O=3ayx`).
The original private key exists only at the developer (深圳麟可科技),
so no re-signed build can pass this check, and no patch inside the 3A APK
can influence a check performed in WeChat's own process.

### Solution: 扫码登录 (WeChat scan-to-login) - works on this build

The app ships a second WeChat login channel that does **not** use the
signature-checked SSO. On the login screen, tap **扫码登录** - the scan
button in the bottom options row (next to 一键登录 and 微信登录).

The app then shows a WeChat QR code ("使用微信扫一扫登录APP", status
"待授权"). This is WeChat's DiffDevOAuth scan-login: pure HTTPS against
open.weixin.qq.com plus your own WeChat doing the authorization - the
WeChat client never checks the calling APK's signature in this flow.

Authorize it with the WeChat that owns your 3A account:

- **Same phone:** WeChat -> 扫一扫 -> tap the 相册 (album) icon -> pick a
  screenshot of the QR -> confirm. (Alternative: send the screenshot to
  文件传输助手, open the image, long-press -> 识别二维码.)
- **Second device:** WeChat -> 扫一扫 -> scan the QR on the screen -> confirm.

The status flips from 待授权 to authorized and the app logs into the
**same WeChat account** - same games, VIP and saves. The received authCode
is POSTed to the very same `login_by_wx` endpoint the SSO flow uses.
If the QR expires (a few minutes), tap 扫码登录 again for a fresh one.

### Recommended: also bind your phone number

After logging in (scan login or any other way): 我的 / user center ->
**账号绑定** -> 绑定手机/微信账号 -> bind your phone via SMS code. From
then on, log in with 手机号 + 验证码 (the login form's default) - the
simplest permanent option. 一键登录 (carrier one-key) also unlocks with a
Chinese SIM.

Alternatives that reach the same account: the official H5 client
`https://play.3ayx.net` (same backend, WeChat web-login + phone binding),
or the official latest APK from the 3A site / app stores as a one-time
"binding vehicle" (its WeChat SSO works because it is genuinely signed).

### WeChat Pay

The direct WeChat-pay button in the recharge flow has the same signature
dependency. Use the 扫码支付 (QR payment) option instead - display-a-QR
flows are signature-free for the same reason as scan-login.

Full analysis and step-by-step guide:
[docs/wechat-login-solution.md](docs/wechat-login-solution.md)

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
