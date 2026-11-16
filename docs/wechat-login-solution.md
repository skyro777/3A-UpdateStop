# WeChat login on the No-Update build - full analysis & solution

## TL;DR

1. The 微信登录 (WeChat SSO) button will **always** fail with `-6` on this
   (or any) re-signed build - the signature check happens inside the WeChat
   app's own process, not in the 3A APK.
2. **Use 扫码登录 instead** - the scan-QR button on the app's login screen.
   It is WeChat's own cross-device login channel (DiffDevOAuth), runs over
   `open.weixin.qq.com` + your own WeChat, and is immune to the APK
   signature problem. It logs into the **same WeChat account**.
3. Then bind a phone number (账号绑定) - future logins become simple
   phone + SMS.

## Why the SSO button fails (and why that can never be patched away)

- The repository's original `3A CLOUD GAMING UPDATE.apk` is the **genuine
  developer-signed 2.7.32 build**. Proof: its signing certificate
  (`CN=Lucers Tu, OU=3ayx, O=3ayx, L=ShenZhen, ST=GuangZhou, C=86`,
  serial 01, valid 2023-11-14 - 2048-11-07; SHA-256
  `bada56d5451ac2d6379c3aa0f1a8a455e4ac161a199a72463bb99bd775645c82`,
  MD5 `32a5bf5af1294ad89bf1d8d25306c64e`) is **byte-identical** to the
  certificate in the official 2.7.36 APK served from the developer's own
  CDN (`cg-dl.3ayx.net/sdk/android/...`), and VirusTotal / Koodous both
  list the file as the official `com.sayx.sagame` app (0/65 detections,
  "Developer: 3ayx"). The filename matches the app's own updater naming.
  That is exactly WHY WeChat login works on it: it is genuinely signed.
- WeChat (com.tencent.mm) verifies login requests by reading the calling
  app's certificate via PackageManager (`getPackageInfo(pkg,
  GET_SIGNATURES)`), hashing it to MD5, and comparing with the signature
  registered on open.weixin.qq.com for app id `wx6c9838146fd2cc29`.
  On mismatch it shows the "Incorrect signature" dialog and returns
  errCode -6 (ERR_BAN) to the caller. Evidence: decompiled WeChat
  (`pluginsdk.model.app` -> `isAppValid` -> "signature is diff" -> -6) and
  the official docs ("若签名验证失败，收到回调 errcode=-6").
- The patched build is signed with `ci/3amod.keystore` (a throwaway key for
  this mod), so the MD5 differs and WeChat refuses. The 3ayx private key
  exists only at the developer (深圳麟可科技 / 3ayx); it is not public
  anywhere (checked: VirusTotal, Koodous, Bing/Baidu/Sogou, grep.app,
  Sourcegraph, Chinese modding forums - circulating "mods" of this app are
  always re-signed and always lose WeChat SSO the same way). Therefore **no
  re-signed build can ever pass the SSO check** (short of rooting the phone
  and spoofing signatures to WeChat system-wide).

## Why 扫码登录 works on a re-signed build

The scan-login uses a completely different channel - WeChat OpenSDK's
**DiffDevOAuth** ("差异化设备授权", designed for devices where the WeChat
app is absent):

1. The app asks 3A's backend for QR-login parameters
   (`LoginController.getWechatLoginCode` -> `APIs.getWechatLoginParamNew`).
2. fluwx calls `com.tencent.mm.opensdk.diffdev.DiffDevOAuthFactory.auth()`
   with (appId, scope, nonceStr, timeStamp, signature) - a plain HTTPS
   conversation with `open.weixin.qq.com`. There is **no com.tencent.mm
   IPC and no PackageManager signature check on the calling APK** in this
   flow.
3. WeChat's server returns a QR image (`onAuthGotQrcode`). The app displays
   it with "使用微信扫一扫登录APP" and status "待授权".
4. You authorize the QR with your own WeChat. Callbacks:
   `onQrcodeScanned` -> `onAuthFinish(authCode)`.
5. The app POSTs the authCode to
   `https://api-3acg.3ayx.net/3agame/resource_manage/login_by_wx` - the
   same endpoint the SSO flow uses - and you are logged into your account.

All of this is present and untouched in the No-Update build (the patch only
breaks two unrelated URL strings: `version_manage/get_info` in libapp.so
and `version_manage/get_hotfix_info` in classes8.dex).

## Step-by-step

### A. Log in with WeChat scan (works on the patched build)

1. Open the app -> login screen.
2. Tap **扫码登录** - the scan button in the bottom options row
   (the row also holds 一键登录 and 微信登录).
3. A QR code appears ("使用微信扫一扫登录APP", "待授权").
4. With the WeChat that owns your 3A account:
   - **Same phone:** WeChat -> 扫一扫 -> tap the 相册 (album) icon ->
     pick a screenshot of the QR -> confirm. (Alternative: send the
     screenshot to 文件传输助手, open the image, long-press ->
     识别二维码.)
   - **Another phone / tablet:** WeChat -> 扫一扫 -> point the camera at
     the QR -> confirm.
5. Confirm the login prompt in WeChat. The app's status flips and it logs
   in automatically.

If the QR expires (a few minutes) or stays at 待授权, tap 扫码登录 again
for a fresh one.

### B. (Recommended) Bind your phone number - permanent simple login

1. After logging in: 我的 / user center -> **账号绑定**
   (绑定手机/微信账号).
2. Enter your phone number -> 获取验证码 -> enter the SMS code -> bind.
3. From now on, log in with 手机号 + 验证码 - that is the login screen's
   default form. 一键登录 (carrier one-key) also works if your SIM is a
   Chinese carrier.

### Alternatives that reach the same account

- **Official H5 client** `https://play.3ayx.net` - same account system
  (same `api-3acg.3ayx.net` backend), WeChat web-login (web appid
  `wx762cc9780b47cc8b`, no signature check) and phone binding
  (`login/index`, `bindPhone` pages in its bundle).
- **Official latest APK** (e.g. 2.7.36, direct links come from the
  `version_manage/get_info` response) as a one-time "binding vehicle":
  its WeChat SSO works (genuinely signed); log in there, bind the phone,
  then return to the No-Update build.

### WeChat Pay note

The direct WeChat-pay button in the recharge flow hits the same signature
wall. The recharge center's 扫码支付 (QR payment) option works fine -
display-a-QR flows are signature-free for the same reason as scan-login.

## Troubleshooting

- **QR expired / stuck at 待授权:** tap 扫码登录 again for a fresh QR.
- **一键登录不可用:** the carrier one-key SDK did not initialize - usually
  means no Chinese SIM; use scan login or phone login.
- **该手机号已注册，无法绑定该账号** when binding: that phone number is
  already tied to another 3A account - use a different number.
- 微信登录 button returns 微信授权失败:-6: expected on this build; use
  扫码登录.
