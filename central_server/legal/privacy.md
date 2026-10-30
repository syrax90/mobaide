# Privacy Policy for Mobaide

**Effective date:** 22 May 2026
**Last updated:** 29 October 2026

This Privacy Policy describes how the Mobaide mobile application ("**Mobaide**", "**the app**", "**we**", "**our**") collects, uses, stores, and discloses information about you ("**you**", "**user**") when you install and use the app on an Android or iOS device, when you use the browser client at `app.mobaide.com`, and when you use the supporting Mobaide central server hosted at `api.mobaide.com`.

Where the two mobile platforms behave differently — the operating-system facility that stores your credentials, the permissions each app requests, how notifications are delivered — this policy says so explicitly. Everything else applies to both.

Mobaide is a developer tool. It lets you remotely interact with [Claude Code](https://claude.ai/code) or [OpenAI Codex](https://developers.openai.com/codex/cli) CLI sessions running on a "Mobaide agent" that you, the user, install and operate on your own workstation. The mobile app communicates with the agent through a relay (the **central server**) operated by the developer of the app.

---

## 1. Who is the data controller?

The data controller is an individual developer publishing the Mobaide app under the trading name "Mobaide":

- **Operator:** Pavel Timonin (individual developer / sole trader, trading as "Mobaide")
- **Contact email:** `paveltimonin90@gmail.com`
- **Privacy contact (same address):** `paveltimonin90@gmail.com`

If you are located in the EU/EEA and you believe we have not handled your data correctly, you have the right to lodge a complaint with your local data protection authority.

---

## 2. Scope of this policy

This policy covers:

- The **Mobaide Android application** (package `com.mobaide.app`) distributed via Google Play.
- The **Mobaide iOS application** (bundle identifier `com.mobaide.app`) for iPhone, distributed via the Apple App Store.
- The **Mobaide central server** (`api.mobaide.com`) operated by the developer.
- The **Mobaide website** (`mobaide.com`).
- The **Mobaide web application** (`app.mobaide.com`), the browser client you sign in to from the website.

This policy does **not** cover:

- The **Mobaide agent** software you install on your own workstation. The agent runs locally on hardware that you control, processes data on your machine, and stores its configuration (including any third-party API keys you provide it) on your local file system. You are the data controller for any data the agent processes on your own device.
- **Claude Code** (provided by Anthropic) and **Claude CLI** sessions. Their data handling is governed by [Anthropic's Privacy Policy](https://www.anthropic.com/legal/privacy). When you send prompts to Claude through Mobaide, those prompts are processed by Anthropic according to Anthropic's terms — Mobaide only relays the bytes.
- **OpenAI Codex CLI** sessions, if you choose to use Codex as your managed agent. Their data handling is governed by [OpenAI's Privacy Policy](https://openai.com/policies/privacy-policy/). When you send prompts to Codex through Mobaide, those prompts are processed by OpenAI according to OpenAI's terms — Mobaide only relays the bytes. If you sign Codex in from inside the app, the OpenAI API key you enter is held only in memory on your phone (never written to your device's storage) and relayed to your own agent, where it is handed to the Codex CLI; it is **never** stored on the Mobaide central server (see Section 3.4). The Codex CLI then saves its own credential file on your own workstation, which is under your control.
- **Linear** (https://linear.app), when you choose to enable the optional "Auto Scan" integration on your own agent. Linear's handling of your workspace data is governed by [Linear's Privacy Policy](https://linear.app/privacy). Your Linear API key is stored locally on your own workstation by the agent; it is **never** transmitted to or stored on the Mobaide central server.

---

## 3. What information we collect

### 3.1 Account information

When you create a Mobaide account (or sign in with Google or Apple), the central server stores:

| Data | Source | Purpose | Where stored |
|---|---|---|---|
| Email address | You (registration form or Google ID token) | Authentication, password reset, verification emails, security notifications | Central server database (EU) |
| Password hash | You (registration / password reset) | Authentication. Passwords are hashed with **Argon2id** before storage; the plaintext password is never persisted. | Central server database (EU) |
| Google account ID (Google `sub` claim) | Google, if you choose "Sign in with Google" | Linking your Google identity to your Mobaide account. We do not store your Google profile name, picture, or any other Google profile field. | Central server database (EU) |
| Apple account ID (Apple `sub` claim) | Apple, if you choose "Sign in with Apple" — offered in the iOS app and in the web application | Linking your Apple identity to your Mobaide account. We request only the `email` scope — not your name — and we store no other Apple profile field. If you choose Apple's **Hide My Email**, the private-relay address Apple issues is what we receive and store as your email address; we never see your real one. | Central server database (EU) |
| Account role (`viewer` / `admin`) | Assigned by the server | Authorization | Central server database (EU) |
| Email-verification status, timestamps | Server-internal | Verifying you control the email address | Central server database (EU) |
| Hashed email-verification / password-reset codes | Generated by the server | Securing the OTP flows. Only hashes are stored; codes are deleted after 24h. | Central server database (EU) |
| Google Ads click attribution: the ad-click identifier your browser arrived with (`gclid`, `wbraid` or `gbraid`), its kind and the time your browser arrived on the site with it, plus a flag recording that you accepted the website's cookie banner | Your browser — only when your account is created in the web application at `app.mobaide.com` after you accepted the cookie banner on `mobaide.com` (see Section 3.5); never from the mobile apps | Reporting to Google Ads, once, that the ad click led to an account that paired its first agent (Sections 5, 6 and 8) | Central server database (EU) — cleared as soon as that one report is made, and in any case within 90 days |

### 3.2 Device & session information

When you use Mobaide from a phone or a browser, the central server stores:

| Data | Purpose | Where stored |
|---|---|---|
| A random device identifier (UUID) generated by the app at first launch | Identifying the device session for re-pairing and revocation. This is **not** an advertising identifier and is **not** the device hardware ID — it is a random UUID generated by Mobaide on first launch. On both **mobile** platforms it is deliberately kept out of device backups, so restoring a backup onto a second device does not clone your device identity; browser storage carries no equivalent backup exclusion. | App (Android: private DataStore, excluded from Auto Backup. iOS: Keychain, "this device only" protection) + central server database. **Web application:** the same kind of random UUID, held in the browser's `localStorage` for `app.mobaide.com` — one per browser profile, and it deliberately outlives signing out so the same browser is recognised as the same device next time |
| Optional device name (e.g. "Anna's Pixel") that you can set in-app | Helping you identify your devices in the agent's paired-devices list | Central server database (with possible local cache on the app). **Web application:** you do not type this one — it is derived from your browser and operating system (e.g. "Chrome on macOS") from the `User-Agent` string the browser already sends |
| Agent identifier, pairing identifier | Routing your traffic to the correct agent | App + central server database |
| Hashed JWT refresh tokens | Keeping you signed in across app restarts | Central server database (hashes only) |
| JWT access & refresh tokens | Authenticating your requests to the central server | App. Android: EncryptedSharedPreferences (AES-256-GCM, Android Keystore master key). iOS: the system Keychain, readable only after the device has been unlocked once since boot, and never synchronised to iCloud Keychain. **Web application:** the short-lived access token lives in the browser tab's `sessionStorage` (cleared when you close the tab); the refresh token is a `Secure`, `HttpOnly`, `SameSite=Strict` cookie (`__Host-mobaide_refresh`, 30 days) that JavaScript on the page cannot read, alongside a small non-secret marker cookie that only tells the page a session may exist |
| **iOS only:** the Apple Push Notification service (APNs) device token issued to this installation, together with the platform name and whether the token targets Apple's sandbox or production environment | Delivering "a session on your agent is waiting for your input" alerts to your iPhone when the app is not in the foreground. The token itself is only an address Apple issues for this app on this device, and is not readable by other apps — but see Section 6 for what the alert we send through Apple actually contains. | App (held in memory) + central server database. Removed server-side when the device is revoked, or as soon as you request account deletion — immediately, not only once the 14-day grace window ends. When you sign out from Settings the app also asks us to remove it, but that request is best-effort — if it does not reach us (for example you are offline), the token stays registered until the device is revoked or Apple retires it |

### 3.3 User content stored on the server

| Data | Purpose | Where stored | Retention |
|---|---|---|---|
| Prompt templates (a name and the body text of each template) | Sync your saved prompts across devices | Central server database (EU) | Until you delete the template or delete your account |

### 3.4 Real-time traffic that we relay but do **not** store

When you operate a Claude Code or OpenAI Codex session from the app, the following data is transported through the central server but is **not** written to disk on the central server:

- Keystrokes you type and terminal output emitted by your agent (Claude Code or Codex session I/O).
- Project and session metadata exchanged between the app and your agent over the WebSocket.
- The OpenAI API key you submit through the in-app Codex sign-in, on its way to your agent. It passes through the server's in-memory routing only and is handed to the Codex CLI on your own workstation; it is never written to the central server's disk, logs, or backups.
- Voice dictation audio and its transcript, if you turn voice input on for an agent and dictate. The app records your microphone only while you dictate, and only while it is on screen. The audio is sent over TLS from your device or browser to your own agent — relayed in memory through the central server, or straight to the agent in direct-connection mode — transcribed on your own computer by an open speech-recognition model, and the text is returned to the app's command field for you to review — it is never sent anywhere on its own. Mobaide never stores the audio or the text: the central server keeps neither, and the agent holds audio only in memory until the transcript is ready and never logs audio or text. No third-party speech service is involved.

One narrow exception, iOS only: when a session needs your attention while the app is not open, we send a push alert whose one-line body names the session — using the title the CLI has set, which may be a few words describing what it is doing, or otherwise your project's name. That line is handed to Apple's push service to deliver. See Sections 3.2 and 6. Note that switching notifications off in iOS Settings stops the alert being *shown* to you, but does not stop it being sent — to stop that, sign out from Settings (which asks us to delete the push address, on a best-effort basis), revoke the device, or request account deletion.

The central server acts as a **transit relay** for this traffic. It forwards bytes between your phone and your agent over an authenticated WebSocket. The traffic is encrypted in transit with TLS (WSS), passes through the server's in-memory routing tables, and is then discarded — it is not appended to any database, log file, or backup. Server-side application logs are emitted at the connection-event level (e.g. "agent X connected", "mobile Y subscribed") and **redact** message payloads.

### 3.5 Information stored only on your device or in your browser

The following is held locally on the device or browser you use Mobaide from. Except where a row in Section 3.2 says otherwise, none of it is stored on our servers:

- Auth tokens (Android: EncryptedSharedPreferences under an AES-256-GCM Android Keystore master key. iOS: the system Keychain).
- Your "last-used project" / "last-used session" UI state.
- Terminal display preferences (display mode, column count).
- Up to 5 recently used custom launch directories per agent — the directory paths you type into the New Session wizard — so the app can offer them again next time. These are paths on **your own workstation**, kept unencrypted in the app's private preferences. The remembered list itself is never sent to our servers — though, like the app's other non-credential preferences, it is included in your device's own platform backup. The path you actually launch does travel through the relay to your own agent as part of that launch, exactly like the rest of the traffic in Section 3.4. You can delete individual entries in-app, and the app deletes **all** of them, for every agent, when you sign out or delete your account.
- The keyboard shortcut chords you have most recently sent to a session, so the shortcut bar can offer them again.
- **iOS only:** which sessions have already alerted you, so the same alert is not raised twice. Also deleted when you sign out.
- A local cache of Linear labels / states for your project (only if you use the Linear Auto Scan feature on your agent), so the in-app filter pickers can render offline.
- **Mobile apps only:** counters and a date that decide when to show the store rating prompt.

In the **web application** the equivalents live in your browser's own storage for that site, and are readable only by pages served from `app.mobaide.com`:

- Per-agent access **and refresh** tokens, in `localStorage`. These authenticate you to one agent, are always re-mintable from your account session, and — as on mobile — carry no expiry by default. Signing out deletes them from the browser.
- The same "up to 5 recently used custom launch directories per agent" described above — paths on **your own workstation** — in `localStorage`. As on mobile, the remembered list is never sent to our servers, and the browser deletes **all** of them, for every agent, when you sign out or delete your account.
- A random device identifier (UUID) for this browser profile, so the browser stays one row on your Devices screen instead of adding one per sign-in. Unlike everything else here it deliberately survives signing out; clearing site data removes it, and the browser is then registered as a new device.
- The relay signing-key fingerprint this browser pinned the first time you paired an agent, used to refuse a later pairing against a different key.
- Your theme choice, selected agent, and the other non-credential UI preferences the screens remember between visits.

Clearing site data for `app.mobaide.com` in your browser removes all of the above and ends the session in that browser; use **Sign out** if you also want the session revoked on our servers.

The Mobaide website (`mobaide.com`) sets one cookie of its own, `portal_csrf`, on the account-deletion pages: a random anti-forgery token carrying no account or tracking information, discarded when you close the browser.

The website's landing page additionally asks you — through a consent banner — whether we may enable **Google Ads conversion measurement**, which tells us when a Google ad brought a visitor to the site. **Nothing is loaded from Google, and no advertising cookie is set, unless you press "Accept"** on that banner. If you do accept, Google's `gtag.js` script loads and Google LLC sets its advertising cookies (such as `_gcl_au`) for `mobaide.com`; if you decline — or simply ignore the banner — no Google code runs at all. Your Accept/Decline choice itself is stored only in your browser's local storage for `mobaide.com` and is never sent to us. Withdrawing consent is as easy as giving it: choose **"Cookie settings"** in the website's footer to reopen the banner, and press **Decline** — this revokes the consent signal and immediately deletes Google's `_gcl_*` advertising cookies from your browser, together with the `mobaide_ads` cookie described next — and, while that cookie is still there to identify it, tells our server to remove the stored click attribution from any account that was created with it. Alternatively, clearing your browser's site data for `mobaide.com` has the same effect, and the banner will then ask again on your next visit.

Accepting the banner also asks our server to set **one first-party cookie of our own**, `mobaide_ads`, for `.mobaide.com` (90 days). It holds the Google Ads click identifier your browser arrived with, if the address you landed on carried one (`gclid`, `wbraid` or `gbraid`), its kind, the time your browser arrived on the page, and a marker that you accepted — nothing else, and nothing from Google. It also carries a one-way fingerprint of the network address you accepted from. Our server signs the whole cookie, so nothing else on the domain can forge it, and it acts on it only when that signature checks out; the click identifier is used whatever network you later sign up from, but the consent to use a hash of your email address instead is honoured only when your sign-up arrives from that same network — so a cookie transplanted from elsewhere can never carry your consent. Its only reader is the `app.mobaide.com` web application, which copies its contents into the request that creates your account (an email registration, or a Google or Apple sign-in that results in a new account); our servers keep that data only when the request creates an account and ignore it everywhere else — although, like any cookie for the domain, your browser attaches it to requests to our `mobaide.com` sites until it expires. What happens to the stored copy is described in Sections 3.1, 6 and 8.

This applies to the `mobaide.com` landing page only — the `app.mobaide.com` web application and the privacy and account-deletion pages load no advertising or analytics code; the web application's one contact with advertising data is reading the `mobaide_ads` cookie at the moment your account is created, as described above. The mobile apps show no cookie banner and load no web tag; their one measurement mechanism is the Firebase Analytics integration described in Section 3.8.

### 3.6 In-app support requests

The app includes an in-app **Help & Support** screen (reachable from the Login and Settings screens, including when you are signed out). It offers two actions:

- **Email support.** When you tap "Email support", the app opens *your own email client* with a message pre-filled to our support address (`support@mobaide.com`) (this does **not** go through the Mobaide central server). The body includes a short diagnostics block to help us troubleshoot: the **app version**, your **device model** (manufacturer and model name, e.g. "Google Pixel 8" on Android or "Apple iPhone17,2" on iOS), the random **device identifier (UUID)** described in Section 3.2, and — **only if you are signed in** — your **account email address**. You can read and edit the whole message before sending, and **nothing is sent unless you yourself send the email**. If no email app is installed, the app only copies the support address to your clipboard and sends nothing. Support emails are processed by us (and our email-delivery provider) solely to answer your request.
- **Ask the community.** When you tap "Ask the community", the app opens our public **GitHub Discussions** page in an in-app browser tab. This is a third-party service operated by GitHub, Inc.; the app sends it none of your data. Reading is anonymous; posting requires a GitHub account. Your activity there is governed by [GitHub's Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).

### 3.7 Information we do **not** collect

For transparency, the app does **not** collect or use:

- Your real name (unless you put it in your prompt-template body yourself).
- Your phone number, address, or any contact details other than the email address you register with.
- Your location. No location permission is requested or used and the apps never read the device's position; the only location-related processing is Google deriving a city-level approximation from the IP address of the analytics requests described in Section 3.8, which we never see.
- Your contacts, calendar, SMS, call logs, or any other personal-information surface on the device.
- Photos. Neither app requests access to your photo library.
- Microphone audio outside voice dictation. The microphone is used only while you dictate with voice input, which you turn on yourself; the audio goes to your own agent and is not stored (Section 3.4). Voice input is off until you enable it, and declining the microphone permission leaves every other feature working.
- Camera imagery. Both apps can scan a pairing QR code, and the two platforms do it differently:
  - **Android:** QR pairing uses the Google Play Services "Code Scanner" module, which runs in a separate Google Play process and only returns the decoded text of the scanned QR code — the Android app does not request the `CAMERA` permission and never receives raw camera frames.
  - **iOS:** there is no equivalent out-of-process scanner, so the iOS app asks for camera permission and reads the camera itself while the scanner sheet is open. Frames are decoded on your device, in the app, purely to read the QR code. They are never recorded, never written to storage, and never sent to the central server or anyone else, and the camera is released as soon as you leave the scanner. You can decline the permission and still pair, using the device-code flow or by adding the agent manually.
- Advertising identifiers, with one Android exception. The iOS app does not use Apple's IDFA, the AdSupport framework, or App Tracking Transparency — its App Store privacy manifest declares that the app performs no tracking — and it has vendor-identifier (IDFV) collection switched off. On Android, the Google Analytics for Firebase SDK described in Section 3.8 reads the resettable Android Advertising ID for install attribution unless you have deleted it in Android's settings; nothing else in the app reads it, and it is never combined with your account.
- Crash reports or product telemetry. The apps do **not** integrate Firebase Crashlytics, Sentry, Amplitude, Mixpanel, the Facebook SDK, or any comparable crash-reporting or behavioural-analytics SDK. The one analytics SDK they carry — Google Analytics for Firebase — is limited to the ad-conversion measurement in Section 3.8 and is never handed your account, session, or agent data.

The in-app support diagnostics described in Section 3.6 are not an exception to the above: they are **not** collected automatically or in the background. They are assembled only when you tap "Email support", are shown to you in your own email client, and reach us only if you choose to send the message.

### 3.8 Ad-conversion measurement inside the mobile apps

Both mobile apps include **Google Analytics for Firebase**, an SDK from Google LLC, for exactly one purpose: telling Google Ads when an app installed from one of our ads led to an account that paired its first agent, so that our app-install campaigns can be measured. It is the in-app counterpart of the website measurement in Section 3.5 — an account created inside the app carries no cookie and no click identifier, so the website mechanism cannot see it.

What the SDK sends to Google:

- A random **Firebase app-instance identifier** generated on your device when the app is first opened. It is not your account ID, it is not the device identifier in Section 3.2, and it changes when you reinstall the app.
- **Android only:** the resettable **Android Advertising ID**, which Google uses to attribute the install to the ad you tapped. You can reset or delete it at any time in Android's settings (Settings → Privacy → Ads); a deleted ID is reported as all zeros and attributes nothing.
- **iOS:** nothing that identifies your device. The app does not link Apple's AdSupport framework, does not read the IDFA, never shows the App Tracking Transparency prompt, and has vendor-identifier (IDFV) collection switched off. Install attribution on iOS relies on Apple's SKAdNetwork, which reports an install to the ad network without identifying you, and on Google's on-device conversion measurement, which matches de-identified, temporary event data (derived from signals such as the IP address and timestamps) on the device itself — Google states that no user-identifying information leaves the device through it.
- Basic device information the SDK collects automatically: device model, operating-system version, app version, language, and the IP address of each request, from which Google derives an approximate (city-level) location. We do not receive the IP address or the location.
- Standard usage events the SDK generates on its own: app opened, session started, app updated. Automatic screen tracking is switched off, so the screens you visit inside the app are not reported.
- **One custom event, `agent_paired`**, sent at most once per account from a given installation, when your account pairs its first agent. Its only parameter records whether the app learned of the pairing live or on a later launch.

What the SDK never receives: your email address, your Mobaide account ID, the device identifier in Section 3.2, your agents, your projects, your prompts, or any terminal traffic. The app never assigns a user ID or user properties to the analytics data, so the events cannot be tied to your Mobaide account and Google Analytics keeps them under the app-instance identifier only.

Consent settings sent with every event: **personalised advertising is denied for everyone** — the data is used to measure whether an ad led to a paired user, not to build an advertising profile or to target you with ads. For users in the **European Economic Area, the United Kingdom and Switzerland**, the advertising-measurement signal (`ad_user_data`) is additionally **denied by default**, so installs from those regions are not attributed to our ads at all; there is no consent prompt in the apps, and this default cannot be switched on from inside the app. Google Analytics still receives the app-instance-level usage events described above in those regions.

This measurement runs in the background without a prompt, on the legal basis described in Section 5. Section 6 describes what Google does with the data, Section 8 how long it is kept, and Section 10 how to limit it.

---

## 4. Permissions used by the app

### Android

The Android app carries the following permissions. The first group is declared by the app itself for its core functionality; the second is merged in by the Google Analytics for Firebase SDK described in Section 3.8; the third is merged in by other libraries the app is built with and reads no personal data:

| Permission | Why it is needed |
|---|---|
| `INTERNET` | Connect to the central server over WSS/HTTPS. |
| `POST_NOTIFICATIONS` | Show the "a session is waiting for your input" alert; Android 13 and later ask you before the first one. |
| `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_SPECIAL_USE` | Keep the events connection to your agent alive while the app is in the background so that those alerts arrive without a push service. |
| `RECORD_AUDIO` | Voice input: record your voice while you dictate, with the app on screen, so your own agent can turn it into text (Section 3.4). Asked the first time you tap the microphone; never used in the background. |
| `com.google.android.gms.permission.AD_ID` | Lets Google Analytics for Firebase read the resettable Android Advertising ID for ad-install attribution (Section 3.8). Delete or reset the ID in Android's settings to withhold it. |
| `ACCESS_ADSERVICES_AD_ID`, `ACCESS_ADSERVICES_ATTRIBUTION` | The Android 13+ Privacy Sandbox counterparts of the row above: reading the Advertising ID and receiving install attribution through the system's ad services, merged in by the same SDK. |
| `com.google.android.finsky.permission.BIND_GET_INSTALL_REFERRER_SERVICE` | Merged in by the same SDK: asks Google Play which ad, if any, led to the installation. |
| `ACCESS_NETWORK_STATE`, `WAKE_LOCK` | Merged in by Google Play services libraries to defer network work until a connection is available. |
| `USE_BIOMETRIC`, `USE_FINGERPRINT` | Merged in by the AndroidX security library that encrypts the auth tokens (Section 9); the app never asks for a fingerprint or face. |

The Android app does **not** declare `CAMERA`, `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION`, `READ_CONTACTS`, `READ_SMS`, `READ_PHONE_STATE`, `READ_EXTERNAL_STORAGE`, or any other sensitive permission.

### iOS

iOS asks you for permission at the moment a feature needs it, and each request carries an explanation. The iOS app asks for the following, and nothing else:

| Permission | When you are asked | Why it is needed |
|---|---|---|
| Camera | Only when you open the pairing-QR scanner | Read the pairing QR code shown on your agent host. Frames are decoded on your device and never stored or transmitted (see Section 3.7). |
| Local network | Only when you connect straight to an agent on your own network ("direct connection" mode), rather than through the relay | Reach your agent at its address on your local network. |
| Notifications | Once, the first time you finish connecting to an agent | Tell you when a session on your agent is waiting for your input. |
| Microphone | Only the first time you dictate with voice input | Record your voice while you dictate, so your own agent can turn it into text (Section 3.4). The audio is not stored. |

All four are optional: declining the camera leaves the device-code and manual pairing paths, declining local network leaves relay mode, declining notifications only means you check the app yourself, and declining the microphone only turns voice input off. The iOS app declares no photo-library, location, or tracking usage, so iOS will never prompt you for them.

In the browser client, your browser asks for microphone access the first time you dictate there, and you can withdraw it in the browser's site settings.

---

## 5. How and why we use the data — legal bases (GDPR)

| Purpose | Data categories used | Legal basis (GDPR Art. 6) |
|---|---|---|
| Create and authenticate your account | Email, password hash, Google `sub`, Apple `sub` | Art. 6(1)(b) — necessary for performance of the contract you enter into when you sign up. |
| Send email-verification codes and password-reset codes | Email, hashed OTP | Art. 6(1)(b) — performance of the contract; Art. 6(1)(c) — compliance with our security obligations. |
| Sync your prompt templates across your devices | Prompt template content, account ID | Art. 6(1)(b) — performance of the contract. |
| Pair your phone with your agent and route traffic between them | Device ID, agent ID, pairing ID, JWT tokens | Art. 6(1)(b) — performance of the contract. |
| Tell you an agent session is waiting for your input, when the app is not open (iOS) | APNs device token, device ID, account ID, session and agent identifiers, and the alert text itself — the session's title or your project's name (see Section 6) | Art. 6(1)(b) — performance of the contract; you can switch the alerts off in iOS Settings at any time. |
| Operational logs (connection events, error events) without payload content | Account ID, agent ID, timestamps, IP address of the requesting connection | Art. 6(1)(f) — legitimate interest in operating, debugging, and securing the service. |
| Detect abuse / brute-force login attempts (rate limiting) | Email, IP address, timestamp | Art. 6(1)(f) — legitimate interest in security. |
| Respond to your in-app support requests | App version, device model, device UUID, and — if you are signed in — your account email, together with the message you write | Art. 6(1)(f) — legitimate interest in providing user support and troubleshooting the app. |
| Measure which Google Ads campaigns bring visitors to the website (the `mobaide.com` landing page only, and only after you accept its cookie banner), and whether such a visitor went on to create an account and pair an agent | Google Ads cookie identifiers and ad-click / conversion events, processed by Google (see Section 3.5); and, if you create an account in the web application, the click identifier stored on your account (Section 3.1) or, when there is none, a SHA-256 hash of your account email address | Art. 6(1)(a) — consent. You are free to decline the banner, and you can withdraw consent at any time as described in Sections 3.5 and 10. |
| Measure which Google Ads app-install campaigns lead to an account that pairs an agent (the mobile apps, Section 3.8) | Firebase app-instance identifier, the Android Advertising ID where available, the automatically collected device and usage data, and the single `agent_paired` event, processed by Google | Art. 6(1)(f) — our legitimate interest in measuring the advertising that funds the app, balanced by the safeguards in Section 3.8: no personalised advertising, no account data, no tracking prompt or advertising identifier on iOS, and the advertising-measurement signal denied by default in the EEA, the UK and Switzerland. You can object at any time (Section 10). |

We do **not** use your account or app data for advertising, profiling, automated decision-making with legal effect, or training machine-learning models. Advertising-related processing in Mobaide is limited to measuring whether our own Google Ads led to a paired user, in two places. The first is the consent-gated conversion measurement on the website's landing page described in Section 3.5 — it runs only if you accept the banner there, and the only account data it ever touches is the click identifier stored at sign-up — or, when there is none, a one-way hash of your email address — sent to Google exactly once, when your account pairs its first agent (Section 6). The second is the Firebase Analytics measurement inside the mobile apps described in Section 3.8, which never touches account data at all.

---

## 6. Sharing and disclosure

We do **not** sell, rent, or trade your personal data. Your information is shared only with the following categories of recipients, strictly for the purposes listed below:

- **Hosting / infrastructure providers** for the central server (located in the EU/EEA). They process data only on our instructions and under a data-processing agreement.
- **Google LLC** — only when you choose "Sign in with Google". Google verifies your Google ID token; we receive your email and your Google `sub` from Google's identity service. Google's processing is governed by [Google's Privacy Policy](https://policies.google.com/privacy).
- **Google Play services** — for distributing the Android app, delivering in-app updates, showing the in-app rating prompt, and providing the GMS Code Scanner module used for QR pairing. Google Play's processing of installation telemetry is governed by [Google Play's terms](https://play.google.com/about/play-terms/).
- **Google LLC (Google Ads)** — only if you accept the cookie banner on the `mobaide.com` landing page. Google then receives the ad-click and conversion events and the cookie identifiers described in Section 3.5, to measure which Google Ads campaigns bring visitors to the site. If you then create a Mobaide account in the web application, Google additionally receives — exactly once, when that account pairs its first agent — the stored ad-click identifier, or, when none was recorded, a SHA-256 hash of your account email address, so that Google Ads can match the conversion to the ad click; that report carries the consent state you gave on the banner (ad user data: granted; ad personalization: denied) and nothing else about your account. Google's processing is governed by [Google's Privacy Policy](https://policies.google.com/privacy). Decline the banner and nothing is shared with Google Ads at all.
- **Google LLC (Google Analytics for Firebase)** — in the mobile apps only, as described in Section 3.8. Google receives the Firebase app-instance identifier, on Android the Advertising ID, the automatically collected device and usage data, and the one `agent_paired` event, and processes them to attribute app installs and first pairings to our Google Ads app-install campaigns. For the analytics data Google acts on our instructions under the [Google Analytics for Firebase Terms](https://firebase.google.com/terms/analytics) and the [Google Ads Data Processing Terms](https://privacy.google.com/businesses/processorterms/); for the advertising measurement itself Google is an independent controller under [Google's Privacy Policy](https://policies.google.com/privacy). Personalised advertising is denied for every user, and the advertising-measurement signal is denied by default in the EEA, the UK and Switzerland.
- **Apple Inc.** — in three distinct roles. (a) **Sign in with Apple**, only if you choose it, and available both in the iOS app and in the `app.mobaide.com` web application: Apple verifies your Apple identity token, and we receive your Apple `sub` and the email address you agreed to share, which may be one of Apple's private-relay addresses. The remaining two roles are iOS-only. (b) **Apple Push Notification service**, which carries the "a session is waiting for your input" alerts to your iPhone. Apple receives, and holds until delivery, the whole alert we send — which is a short headline plus one line naming the session, using the session's title where it has one (a coding CLI often sets that title to a few words about what it is currently doing) or otherwise your project's name, together with the session and agent identifiers. This is the one place where something derived from your session leaves our relay in stored form, and it is the price of an alert that is useful on a lock screen; the terminal traffic itself is still only relayed and discarded as described in Section 3.4. Switching notifications off in iOS Settings suppresses the alert on your screen but does not stop it being sent. Signing out from Settings asks us to delete the push address, but that request is best-effort — if it does not reach us, revoking the device or requesting account deletion is what reliably stops the alerts — the latter takes effect immediately, ahead of the 14-day grace window. (c) **App Store** distribution of the iOS app and its rating prompt. Apple's processing is governed by [Apple's Privacy Policy](https://www.apple.com/legal/privacy/).
- **Email-delivery providers** (SMTP transactional email) that send our verification, password-reset, and security-notification emails, and that carry the support emails you send us through the in-app "Email support" action. These providers process the recipient address and the email body strictly to deliver the message.
- **Anthropic** — when you type a prompt that is routed through your own agent to Claude Code. The content you send to Claude is processed by Anthropic according to [Anthropic's Privacy Policy](https://www.anthropic.com/legal/privacy). Mobaide is not a party to your Anthropic relationship; we merely relay the bytes.
- **OpenAI** — when you choose to use Codex as your managed agent and type a prompt that is routed through your own agent to Codex. The content you send to Codex is processed by OpenAI according to [OpenAI's Privacy Policy](https://openai.com/policies/privacy-policy/). Mobaide is not a party to your OpenAI relationship; we merely relay the bytes.
- **GitHub, Inc.** — when you tap "Ask the community", the app opens our public GitHub Discussions page in your browser. The app does not send GitHub any of your data; anything you choose to post there is provided by you and processed by GitHub under [GitHub's Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).
- **Public authorities**, if compelled by a valid legal order and only to the extent legally required.

Beyond the Google Ads measurement described in Sections 3.5, 3.8 and 6 — the website's consent-gated tag, the one-time first-pairing report, and the Firebase Analytics events from the mobile apps — we do **not** share your data with advertisers, data brokers, or analytics aggregators.

---

## 7. International data transfers

The central server, its database, and its backups are hosted in the **European Union / European Economic Area (EU/EEA)**.

Some of the third parties listed in Section 6 (in particular Google, Apple, Anthropic, and OpenAI) may process data outside the EU/EEA. Where such processing occurs, those providers rely on the European Commission's Standard Contractual Clauses (SCCs) or on an adequacy decision to safeguard the transfer. Links to those providers' transfer-impact disclosures are included in Section 6.

---

## 8. How long we keep your data

| Data | Retention period |
|---|---|
| Account record (email, password hash, Google `sub`, Apple `sub`) | Until you delete your account (see Section 10). |
| Hashed mobile refresh tokens | Invalidated as soon as you revoke the device, reset your password, or delete your account. By default these tokens carry no expiry, so signing out on the phone — which deletes the app's own copies — does not by itself invalidate the stored hash; revoking the device does. The hash itself is then kept, marked revoked, as a security audit record until your account is deleted, at which point it is purged with the rest of your data. |
| APNs device token (iOS) | Until you revoke the device or request account deletion (dropped immediately on request, not only once the 14-day grace window ends). Signing out from Settings also asks us to remove it, on a best-effort basis. Apple additionally retires tokens on its own, and a token Apple reports as gone is deleted on our side. |
| Hashed pairing tokens | At most 5 minutes (the lifetime of a QR pairing nonce). Tokens that have served their purpose are scrubbed shortly after consumption. |
| Email-verification / password-reset OTP token hashes | Active code: up to its TTL (15 min for email verification, 30 min for password reset). Consumed code: at most 24 hours, then permanently deleted. |
| Prompt templates | Until you delete them, or — if you delete your account — purged together with your account: immediately for *Immediate* deletion, or at the end of the 14-day grace window for *Scheduled* deletion. |
| Operational logs (connection-event level, no payload) | Up to 90 days, then permanently deleted. |
| Google Ads cookies on `mobaide.com` (set only after you accept the website's cookie banner — see Section 3.5) | Set by Google in your browser with lifetimes Google determines — typically up to 90 days for the `_gcl_*` cookies. You can remove them at any time — pressing **Decline** after reopening the banner via the website footer's "Cookie settings" deletes them immediately, as does clearing your browser's site data for `mobaide.com`; your Accept/Decline choice itself stays in your browser's local storage until you change or clear it. The ad-click and conversion events Google receives are retained by Google under [Google's Privacy Policy](https://policies.google.com/privacy); what we hold ourselves is described in the next row. |
| Google Ads click attribution on your account record (Section 3.1) | Cleared as soon as the one-time first-pairing report has been sent or has definitively failed, and in any case no later than 90 days after the click or after your sign-up, whichever comes first, by a server job that runs hourly and at every restart; also removed on request (Section 10). The `mobaide_ads` cookie in your own browser lasts 90 days and is deleted at once when you press **Decline**. |
| Google Analytics for Firebase data from the mobile apps (Section 3.8) | Held by Google under our Google Analytics property settings: event-level data for 2 months, after which only aggregated reports remain; Google Signals (linking with signed-in Google accounts across devices) is switched off. On your device the app keeps only a small marker recording that the one-time `agent_paired` event has already been sent for your account, so that it is never sent twice; it lives in the app's private storage, is excluded from device backups and transfers, and disappears when you clear the app's data or uninstall the app. |
| Deletion audit record (account identifier, timestamps, request source and outcome) | Retained indefinitely as a minimal proof-of-deletion record, in order to demonstrate compliance with GDPR Art. 17 ("right to erasure"). |
| Support correspondence (the email you send to our support address and our replies) | Kept only as long as necessary to handle your request and for a reasonable period afterwards, then deleted. |
| Server-side backups | Rolled over within 30 days. |

After account deletion, all directly identifying data (other than the minimal deletion audit record described above) is purged from the live database within 30 days; backup rotations remove it within an additional 30 days.

---

## 9. Security

We apply industry-standard safeguards:

- All network traffic between the app, the central server, and your agent uses **TLS 1.2+** (HTTPS / WSS).
- Passwords are hashed with **Argon2id**. Plaintext passwords are never stored, logged, or sent to any third party.
- Pairing tokens, refresh tokens, and OTP codes are stored only as **SHA-256 / HMAC-SHA-256 hashes**; the raw tokens are not retained.
- On **Android**, auth tokens are stored in **EncryptedSharedPreferences** under an AES-256-GCM master key managed by the Android Keystore. The app's backup rules exclude both the encrypted auth file and the device-identity DataStore from Android Auto Backup.
- On **iOS**, auth tokens are stored in the system **Keychain**, protected so that they are readable only after you have unlocked the device at least once since it booted, and never synchronised to iCloud Keychain. Your device identity is held to a stricter class again — "this device only" — so it is excluded from backups and never migrates to a replacement device. Be aware of the corresponding limit: unlike the device identity, the Keychain items holding your tokens **are** included in an encrypted iPhone backup, so a party who can restore your encrypted backup could restore your signed-in session with it. Signing out deletes the app's copies on that device, but it does not invalidate them on our server, and by default these tokens carry no expiry. If you are concerned about an old backup: revoking that device (Settings → Devices) invalidates its refresh tokens and stops its push alerts, but an access token that was already issued carries no expiry by default and cannot be withdrawn once minted. Deleting your account is what invalidates every credential tied to it.
- In the **web application**, the refresh token is held in a `Secure`, `HttpOnly`, `SameSite=Strict`, `__Host-`-prefixed cookie that page JavaScript cannot read, and the short-lived account access token is kept per-tab in `sessionStorage` rather than in long-lived browser storage. Per-agent tokens are the exception — they sit in `localStorage` and are deleted when you sign out. Cookie-authenticated requests additionally carry a custom header, which a cross-site page cannot set. Signing out — and scheduling account deletion — revokes the refresh token on our server, not only in the browser.
- On first pairing, all three clients — both mobile apps and the browser client — pin the central server's manifest-signing public-key fingerprint (SHA-256). Subsequent pairings against the same relay must match the pinned hash; a mismatch hard-fails and cannot be bypassed by user confirmation.
- The mobile↔server WebSocket and the agent↔server WebSocket authenticate independently with separate JWTs / bearer tokens.

No system is perfectly secure. If we become aware of a personal-data breach that is likely to result in a risk to your rights, we will notify the competent supervisory authority within 72 hours (GDPR Art. 33) and, where required, inform affected users without undue delay.

---

## 10. Your rights

If you are in the EU/EEA, the UK, or another jurisdiction with comparable rights, you have the right to:

- **Access** the personal data we hold about you.
- **Rectify** inaccurate data.
- **Erase** your data ("right to be forgotten"). You can delete your account at any time:
  - **In-app:** Settings → **Danger zone** → **Delete account**. You pick one of two modes:
    - *Scheduled* (default): a 14-day cancellable grace window. You can cancel via the in-app banner, the web application, or the one-click cancel link sent in the confirmation email.
    - *Immediate*: an irreversible cascade purge executed synchronously, with no grace window.
    You will be asked to re-authenticate before the deletion is accepted — with your password, or with a fresh Google sign-in, or, where your account is linked to Apple, a fresh Sign in with Apple — issued within the last 5 minutes.
  - **Web application:** open `https://app.mobaide.com/account` (you will be asked to sign in first), scroll to **Danger zone**, and confirm. The same two modes and re-authentication requirements apply.
  - **By email:** send `paveltimonin90@gmail.com` a deletion request from the email address registered to your Mobaide account. We will complete the deletion within 30 days and send you a confirmation.
  - **Public information page (no sign-in required):** `https://mobaide.com/portal/delete-account-info` describes the steps and timing. This is also the URL listed on the Google Play "Data deletion" entry.

  Once the deletion completes (immediately for *Immediate* mode, at the end of the 14-day window for *Scheduled* mode), your account record, paired devices, prompt templates, refresh tokens, and all dependent rows are purged from the live database. A minimal audit record of the deletion request is retained as described in Section 8.
- **Restrict** processing in specific circumstances.
- **Object** to processing carried out under our legitimate interest (Art. 6(1)(f)) — in particular our operational logs.
- **Portability** — receive a machine-readable copy of the data you provided to us.
- **Withdraw consent** at any time (where processing relies on consent). For the website's Google Ads measurement, reopen the banner via the footer's **Cookie settings** and press **Decline** — this deletes the `mobaide_ads` cookie along with Google's and removes the stored click attribution from any account created with that cookie. If the cookie has already expired or you decline from another browser, email us and we will remove it (it is in any case cleared after the one-time report or after 90 days, see Section 8).
- **Limit the in-app measurement** described in Section 3.8. On Android, delete or reset your Advertising ID in Settings → Privacy → Ads, after which nothing the app sends can be attributed to an ad. On iOS there is nothing to switch off — the app reads no advertising identifier and performs no tracking. Everywhere, personalised advertising is already denied, and in the EEA, the UK and Switzerland the advertising-measurement signal is denied by default. You can also object to this processing altogether by emailing us; because the analytics data is not linked to your account, deleting it on Google's side depends on identifying your app instance, and we will walk you through that.
- **Lodge a complaint** with your local supervisory authority.

To exercise any of these rights, email `paveltimonin90@gmail.com`. We will respond within 30 days. We may need to verify that the requester is the account holder before acting.

---

## 11. Children's privacy

Mobaide is a developer tool intended for users **aged 13 and over**, and is not directed at children under 13. We do not knowingly collect personal data from children under 13. If you believe a child under 13 has created an account, please contact `paveltimonin90@gmail.com` and we will delete the account and associated data promptly.

In the EU/EEA, the minimum age for consenting to information-society services without parental consent varies by member state (between 13 and 16); if you are below the applicable threshold in your country, you must have your parent's or guardian's permission to use Mobaide.

---

## 12. Open-source components

The apps are built with, among others, the following third-party components. None of them, as integrated, transmits personal data to any party other than as described in this policy.

**Android:**

- AndroidX Jetpack libraries (Apache-2.0)
- Jetpack Compose & Material 3 (Apache-2.0)
- OkHttp (Apache-2.0) — HTTPS / WebSocket transport
- Kotlinx Serialization (Apache-2.0)
- Hilt / Dagger (Apache-2.0)
- AndroidX Security Crypto (Apache-2.0) — EncryptedSharedPreferences
- Google Credential Manager + Google ID (Apache-2.0) — Sign in with Google
- Google Play In-App Update (Play Core SDK Terms of Service)
- Google Play In-App Review (Play Core SDK Terms of Service) — rating prompt
- Google Play Services Code Scanner (Apache-2.0) — QR scanning
- Google Analytics for Firebase (Apache-2.0 SDK around Google's proprietary `play-services-measurement` binary) — ad-conversion measurement (Section 3.8)
- Termux `terminal-view` (Apache-2.0, vendored fork) — terminal renderer
- Calvin-LL/Reorderable (MIT) — drag-and-drop reordering

**iOS:**

- SwiftTerm (MIT) — terminal renderer
- GoogleSignIn-iOS (Apache-2.0) — Sign in with Google, together with its dependencies AppAuth-iOS, GTMAppAuth, GoogleUtilities, gtm-session-fetcher, Google App Check, interop-ios-for-google-sdks, and Google Promises (all Apache-2.0)
- swift-argument-parser (Apache-2.0)
- Firebase Apple SDK — `FirebaseAnalytics` (Apache-2.0) together with Google's proprietary `GoogleAppMeasurement` and on-device conversion measurement binaries and its nanopb (zlib) and GoogleUtilities (Apache-2.0) dependencies — ad-conversion measurement (Section 3.8)

Networking, the Keychain, the camera-based QR scanner, Sign in with Apple, push notifications, and the rating prompt on iOS all use Apple's own system frameworks rather than third-party libraries.

---

## 13. Changes to this policy

We may update this Privacy Policy from time to time. When we do, we will change the **Last updated** date at the top and, for material changes, notify you in-app or by email before the new version takes effect. The current version is always available at `https://mobaide.com/privacy.html`.

---

## 14. Contact

For general questions or support, please email:

**`support@mobaide.com`**

For questions about this policy or about your personal data (including exercising the rights described in Section 10), please email:

**`paveltimonin90@gmail.com`**
