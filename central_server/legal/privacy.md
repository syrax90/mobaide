# Privacy Policy for Mobaide

**Effective date:** 22 May 2026
**Last updated:** 10 August 2026

This Privacy Policy describes how the Mobaide mobile application ("**Mobaide**", "**the app**", "**we**", "**our**") collects, uses, stores, and discloses information about you ("**you**", "**user**") when you install and use the app on an Android or iOS device, and when you use the supporting Mobaide central server hosted at `api.mobaide.com`.

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
| Apple account ID (Apple `sub` claim) | Apple, if you choose "Sign in with Apple" — offered in the iOS app and on the web portal | Linking your Apple identity to your Mobaide account. We request only the `email` scope — not your name — and we store no other Apple profile field. If you choose Apple's **Hide My Email**, the private-relay address Apple issues is what we receive and store as your email address; we never see your real one. | Central server database (EU) |
| Account role (`viewer` / `admin`) | Assigned by the server | Authorization | Central server database (EU) |
| Email-verification status, timestamps | Server-internal | Verifying you control the email address | Central server database (EU) |
| Hashed email-verification / password-reset codes | Generated by the server | Securing the OTP flows. Only hashes are stored; codes are deleted after 24h. | Central server database (EU) |

### 3.2 Device & session information

When you pair your phone with an agent, the central server stores:

| Data | Purpose | Where stored |
|---|---|---|
| A random device identifier (UUID) generated by the app at first launch | Identifying the device session for re-pairing and revocation. This is **not** an advertising identifier and is **not** the device hardware ID — it is a random UUID generated by Mobaide on first launch. On both platforms it is deliberately kept out of device backups, so restoring a backup onto a second device does not clone your device identity. | App (Android: private DataStore, excluded from Auto Backup. iOS: Keychain, "this device only" protection) + central server database |
| Optional device name (e.g. "Anna's Pixel") that you can set in-app | Helping you identify your devices in the agent's paired-devices list | Central server database (with possible local cache on the app) |
| Agent identifier, pairing identifier | Routing your traffic to the correct agent | App + central server database |
| Hashed JWT refresh tokens | Keeping you signed in across app restarts | Central server database (hashes only) |
| JWT access & refresh tokens | Authenticating your requests to the central server | App. Android: EncryptedSharedPreferences (AES-256-GCM, Android Keystore master key). iOS: the system Keychain, readable only after the device has been unlocked once since boot, and never synchronised to iCloud Keychain |
| **iOS only:** the Apple Push Notification service (APNs) device token issued to this installation, together with the platform name and whether the token targets Apple's sandbox or production environment | Delivering "a session on your agent is waiting for your input" alerts to your iPhone when the app is not in the foreground. The token itself is only an address Apple issues for this app on this device, and is not readable by other apps — but see Section 6 for what the alert we send through Apple actually contains. | App (held in memory) + central server database. Removed server-side when the device is revoked and when your account is deleted. When you sign out from Settings the app also asks us to remove it, but that request is best-effort — if it does not reach us (for example you are offline), the token stays registered until the device is revoked or Apple retires it |

### 3.3 User content stored on the server

| Data | Purpose | Where stored | Retention |
|---|---|---|---|
| Prompt templates (a name and the body text of each template) | Sync your saved prompts across devices | Central server database (EU) | Until you delete the template or delete your account |

### 3.4 Real-time traffic that we relay but do **not** store

When you operate a Claude Code or OpenAI Codex session from the app, the following data is transported through the central server but is **not** written to disk on the central server:

- Keystrokes you type and terminal output emitted by your agent (Claude Code or Codex session I/O).
- Project and session metadata exchanged between the app and your agent over the WebSocket.
- The OpenAI API key you submit through the in-app Codex sign-in, on its way to your agent. It passes through the server's in-memory routing only and is handed to the Codex CLI on your own workstation; it is never written to the central server's disk, logs, or backups.

One narrow exception, iOS only: when a session needs your attention while the app is not open, we send a push alert whose one-line body names the session — using the title the CLI has set, which may be a few words describing what it is doing, or otherwise your project's name. That line is handed to Apple's push service to deliver. See Sections 3.2 and 6. Note that switching notifications off in iOS Settings stops the alert being *shown* to you, but does not stop it being sent — to stop that, sign out from Settings (which asks us to delete the push address, on a best-effort basis), revoke the device, or delete your account.

The central server acts as a **transit relay** for this traffic. It forwards bytes between your phone and your agent over an authenticated WebSocket. The traffic is encrypted in transit with TLS (WSS), passes through the server's in-memory routing tables, and is then discarded — it is not appended to any database, log file, or backup. Server-side application logs are emitted at the connection-event level (e.g. "agent X connected", "mobile Y subscribed") and **redact** message payloads.

### 3.5 Information stored only on your device

The following information stays on your phone and is not transmitted to the central server:

- Auth tokens (Android: EncryptedSharedPreferences under an AES-256-GCM Android Keystore master key. iOS: the system Keychain).
- Your "last-used project" / "last-used session" UI state.
- Terminal display preferences (display mode, column count).
- Up to 5 recently used custom launch directories per agent — the directory paths you type into the New Session wizard — so the app can offer them again next time. These are paths on **your own workstation**, kept unencrypted in the app's private preferences. The remembered list itself is never sent to our servers — though, like the app's other non-credential preferences, it is included in your device's own platform backup. The path you actually launch does travel through the relay to your own agent as part of that launch, exactly like the rest of the traffic in Section 3.4. You can delete individual entries in-app, and the app deletes **all** of them, for every agent, when you sign out or delete your account.
- The keyboard shortcut chords you have most recently sent to a session, so the shortcut bar can offer them again.
- **iOS only:** which sessions have already alerted you, so the same alert is not raised twice. Also deleted when you sign out.
- A local cache of Linear labels / states for your project (only if you use the Linear Auto Scan feature on your agent), so the in-app filter pickers can render offline.

### 3.6 In-app support requests

The app includes an in-app **Help & Support** screen (reachable from the Login and Settings screens, including when you are signed out). It offers two actions:

- **Email support.** When you tap "Email support", the app opens *your own email client* with a message pre-filled to our support address (`support@mobaide.com`) (this does **not** go through the Mobaide central server). The body includes a short diagnostics block to help us troubleshoot: the **app version**, your **device model** (manufacturer and model name, e.g. "Google Pixel 8" on Android or "Apple iPhone17,2" on iOS), the random **device identifier (UUID)** described in Section 3.2, and — **only if you are signed in** — your **account email address**. You can read and edit the whole message before sending, and **nothing is sent unless you yourself send the email**. If no email app is installed, the app only copies the support address to your clipboard and sends nothing. Support emails are processed by us (and our email-delivery provider) solely to answer your request.
- **Ask the community.** When you tap "Ask the community", the app opens our public **GitHub Discussions** page in an in-app browser tab. This is a third-party service operated by GitHub, Inc.; the app sends it none of your data. Reading is anonymous; posting requires a GitHub account. Your activity there is governed by [GitHub's Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).

### 3.7 Information we do **not** collect

For transparency, the app does **not** collect or use:

- Your real name (unless you put it in your prompt-template body yourself).
- Your phone number, address, or any contact details other than the email address you register with.
- Your location (no location permission is requested or used).
- Your contacts, calendar, SMS, call logs, or any other personal-information surface on the device.
- Photos or microphone input. Neither app requests access to your photo library or your microphone.
- Camera imagery. Both apps can scan a pairing QR code, and the two platforms do it differently:
  - **Android:** QR pairing uses the Google Play Services "Code Scanner" module, which runs in a separate Google Play process and only returns the decoded text of the scanned QR code — the Android app does not request the `CAMERA` permission and never receives raw camera frames.
  - **iOS:** there is no equivalent out-of-process scanner, so the iOS app asks for camera permission and reads the camera itself while the scanner sheet is open. Frames are decoded on your device, in the app, purely to read the QR code. They are never recorded, never written to storage, and never sent to the central server or anyone else, and the camera is released as soon as you leave the scanner. You can decline the permission and still pair, using the device-code flow or by adding the agent manually.
- Advertising identifiers. The Android Advertising ID is not read, and the iOS app does not use Apple's IDFA, the AdSupport framework, or App Tracking Transparency — its App Store privacy manifest declares that the app performs no tracking.
- Crash reports or telemetry from third-party SDKs. The app does **not** integrate Firebase Analytics, Firebase Crashlytics, Google Analytics, Sentry, Amplitude, Mixpanel, Facebook SDK, or any comparable analytics/tracking SDK.

The in-app support diagnostics described in Section 3.6 are not an exception to the above: they are **not** collected automatically or in the background. They are assembled only when you tap "Email support", are shown to you in your own email client, and reach us only if you choose to send the message.

---

## 4. Permissions used by the app

### Android

The Android app declares the following permission, used strictly for its core functionality:

| Permission | Why it is needed |
|---|---|
| `INTERNET` | Connect to the central server over WSS/HTTPS. |

The Android app does **not** declare `CAMERA`, `RECORD_AUDIO`, `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION`, `READ_CONTACTS`, `READ_SMS`, `READ_PHONE_STATE`, `READ_EXTERNAL_STORAGE`, or any other sensitive permission.

### iOS

iOS asks you for permission at the moment a feature needs it, and each request carries an explanation. The iOS app asks for the following, and nothing else:

| Permission | When you are asked | Why it is needed |
|---|---|---|
| Camera | Only when you open the pairing-QR scanner | Read the pairing QR code shown on your agent host. Frames are decoded on your device and never stored or transmitted (see Section 3.7). |
| Local network | Only when you connect straight to an agent on your own network ("direct connection" mode), rather than through the relay | Reach your agent at its address on your local network. |
| Notifications | Once, the first time you finish connecting to an agent | Tell you when a session on your agent is waiting for your input. |

All three are optional: declining the camera leaves the device-code and manual pairing paths, declining local network leaves relay mode, and declining notifications only means you check the app yourself. The iOS app declares no photo-library, microphone, location, or tracking usage, so iOS will never prompt you for them.

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

We do **not** use your data for advertising, profiling, automated decision-making with legal effect, or training machine-learning models.

---

## 6. Sharing and disclosure

We do **not** sell, rent, or trade your personal data. Your information is shared only with the following categories of recipients, strictly for the purposes listed below:

- **Hosting / infrastructure providers** for the central server (located in the EU/EEA). They process data only on our instructions and under a data-processing agreement.
- **Google LLC** — only when you choose "Sign in with Google". Google verifies your Google ID token; we receive your email and your Google `sub` from Google's identity service. Google's processing is governed by [Google's Privacy Policy](https://policies.google.com/privacy).
- **Google Play services** — for distributing the Android app, delivering in-app updates, and providing the GMS Code Scanner module used for QR pairing. Google Play's processing of installation telemetry is governed by [Google Play's terms](https://play.google.com/about/play-terms/).
- **Apple Inc.** — in three distinct roles. (a) **Sign in with Apple**, only if you choose it, and available both in the iOS app and on the `mobaide.com` web portal: Apple verifies your Apple identity token, and we receive your Apple `sub` and the email address you agreed to share, which may be one of Apple's private-relay addresses. The remaining two roles are iOS-only. (b) **Apple Push Notification service**, which carries the "a session is waiting for your input" alerts to your iPhone. Apple receives, and holds until delivery, the whole alert we send — which is a short headline plus one line naming the session, using the session's title where it has one (a coding CLI often sets that title to a few words about what it is currently doing) or otherwise your project's name, together with the session and agent identifiers. This is the one place where something derived from your session leaves our relay in stored form, and it is the price of an alert that is useful on a lock screen; the terminal traffic itself is still only relayed and discarded as described in Section 3.4. Switching notifications off in iOS Settings suppresses the alert on your screen but does not stop it being sent. Signing out from Settings asks us to delete the push address, but that request is best-effort — if it does not reach us, revoking the device or deleting your account is what reliably stops the alerts. (c) **App Store** distribution of the iOS app. Apple's processing is governed by [Apple's Privacy Policy](https://www.apple.com/legal/privacy/).
- **Email-delivery providers** (SMTP transactional email) that send our verification, password-reset, and security-notification emails, and that carry the support emails you send us through the in-app "Email support" action. These providers process the recipient address and the email body strictly to deliver the message.
- **Anthropic** — when you type a prompt that is routed through your own agent to Claude Code. The content you send to Claude is processed by Anthropic according to [Anthropic's Privacy Policy](https://www.anthropic.com/legal/privacy). Mobaide is not a party to your Anthropic relationship; we merely relay the bytes.
- **OpenAI** — when you choose to use Codex as your managed agent and type a prompt that is routed through your own agent to Codex. The content you send to Codex is processed by OpenAI according to [OpenAI's Privacy Policy](https://openai.com/policies/privacy-policy/). Mobaide is not a party to your OpenAI relationship; we merely relay the bytes.
- **GitHub, Inc.** — when you tap "Ask the community", the app opens our public GitHub Discussions page in your browser. The app does not send GitHub any of your data; anything you choose to post there is provided by you and processed by GitHub under [GitHub's Privacy Statement](https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement).
- **Public authorities**, if compelled by a valid legal order and only to the extent legally required.

We do **not** share your data with advertisers, data brokers, or analytics aggregators.

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
| APNs device token (iOS) | Until you revoke the device or delete your account. Signing out from Settings also asks us to remove it, on a best-effort basis. Apple additionally retires tokens on its own, and a token Apple reports as gone is deleted on our side. |
| Hashed pairing tokens | At most 5 minutes (the lifetime of a QR pairing nonce). Tokens that have served their purpose are scrubbed shortly after consumption. |
| Email-verification / password-reset OTP token hashes | Active code: up to its TTL (15 min for email verification, 30 min for password reset). Consumed code: at most 24 hours, then permanently deleted. |
| Prompt templates | Until you delete them, or — if you delete your account — purged together with your account: immediately for *Immediate* deletion, or at the end of the 14-day grace window for *Scheduled* deletion. |
| Operational logs (connection-event level, no payload) | Up to 90 days, then permanently deleted. |
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
- On first QR pairing, both mobile apps pin the central server's manifest-signing public-key fingerprint (SHA-256). Subsequent pairings against the same relay must match the pinned hash; a mismatch hard-fails and cannot be bypassed by user confirmation.
- The mobile↔server WebSocket and the agent↔server WebSocket authenticate independently with separate JWTs / bearer tokens.

No system is perfectly secure. If we become aware of a personal-data breach that is likely to result in a risk to your rights, we will notify the competent supervisory authority within 72 hours (GDPR Art. 33) and, where required, inform affected users without undue delay.

---

## 10. Your rights

If you are in the EU/EEA, the UK, or another jurisdiction with comparable rights, you have the right to:

- **Access** the personal data we hold about you.
- **Rectify** inaccurate data.
- **Erase** your data ("right to be forgotten"). You can delete your account at any time:
  - **In-app:** Settings → **Danger zone** → **Delete account**. You pick one of two modes:
    - *Scheduled* (default): a 14-day cancellable grace window. You can cancel via the in-app banner, the web portal, or the one-click cancel link sent in the confirmation email.
    - *Immediate*: an irreversible cascade purge executed synchronously, with no grace window.
    You will be asked to re-authenticate before the deletion is accepted — with your password, or with a fresh Google sign-in, or, where your account is linked to Apple, a fresh Sign in with Apple — issued within the last 5 minutes.
  - **Web portal:** sign in at `https://mobaide.com/portal/account`, open **Danger zone**, and confirm. The same two modes and re-authentication requirements apply.
  - **By email:** send `paveltimonin90@gmail.com` a deletion request from the email address registered to your Mobaide account. We will complete the deletion within 30 days and send you a confirmation.
  - **Public information page (no sign-in required):** `https://mobaide.com/portal/delete-account-info` describes the steps and timing. This is also the URL listed on the Google Play "Data deletion" entry.

  Once the deletion completes (immediately for *Immediate* mode, at the end of the 14-day window for *Scheduled* mode), your account record, paired devices, prompt templates, refresh tokens, and all dependent rows are purged from the live database. A minimal audit record of the deletion request is retained as described in Section 8.
- **Restrict** processing in specific circumstances.
- **Object** to processing carried out under our legitimate interest (Art. 6(1)(f)) — in particular our operational logs.
- **Portability** — receive a machine-readable copy of the data you provided to us.
- **Withdraw consent** at any time (where processing relies on consent).
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
- Google Play In-App Update (Apache-2.0)
- Google Play Services Code Scanner (Apache-2.0) — QR scanning
- Termux `terminal-view` (Apache-2.0, vendored fork) — terminal renderer
- Calvin-LL/Reorderable (MIT) — drag-and-drop reordering

**iOS:**

- SwiftTerm (MIT) — terminal renderer
- GoogleSignIn-iOS (Apache-2.0) — Sign in with Google, together with its dependencies AppAuth-iOS, GTMAppAuth, GoogleUtilities, gtm-session-fetcher, Google App Check, interop-ios-for-google-sdks, and Google Promises (all Apache-2.0)
- swift-argument-parser (Apache-2.0)

Networking, the Keychain, the camera-based QR scanner, Sign in with Apple, and push notifications on iOS all use Apple's own system frameworks rather than third-party libraries.

---

## 13. Changes to this policy

We may update this Privacy Policy from time to time. When we do, we will change the **Last updated** date at the top and, for material changes, notify you in-app or by email before the new version takes effect. The current version is always available at `https://mobaide.com/privacy.html`.

---

## 14. Contact

For general questions or support, please email:

**`support@mobaide.com`**

For questions about this policy or about your personal data (including exercising the rights described in Section 10), please email:

**`paveltimonin90@gmail.com`**
