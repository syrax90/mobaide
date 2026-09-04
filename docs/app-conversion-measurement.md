# In-app ad-conversion measurement (Firebase, the "Agent paired" app lane)

Google Ads optimises every Mobaide campaign for one conversion: **an account
pairs its first agent**. The relay reports it for *web* sign-ups through the
Data Manager API (see *Google Ads conversion upload* in
[deployment.md](deployment.md)). That lane cannot see an account created
inside a mobile app — no cookie, no click id — and App-install campaigns can
only be attributed through Google's own chain: **Google Analytics for
Firebase → GA4 → Google Ads**. This document is that second lane: what the
apps send, how "once per account" is enforced on a device, how the untracked
Firebase config files reach a build, and what the two store consoles must be
told. The privacy policy's Section 3.8 (`central_server/legal/privacy.md`) is
the user-facing statement of the same facts and must stay in sync with it.

## Wire contract

Both signals come from the same compare-and-set in
`central_server/central_server/pairing/router.py::_complete_pairing`
(`db.mark_user_first_paired`, true exactly once per account, never cleared —
see *The first-pairing marker* in [qr-pairing.md](qr-pairing.md)):

- **User event** `account.first_agent_paired`, payload `{"agent_id": str}`,
  pushed through `broadcast_user_event` to every `ws/user-events` socket of
  the account, inside its own `try/except` so a fault can never fail a
  delivered pairing (the device-code wrapper would reopen a consumed code).
  Additive — no protocol bump; every client already ignores unknown kinds.
- **REST** `GET /api/auth/me` (dashboard/user JWT, any role) →
  `{"user_id", "email", "role", "created_at", "scheduled_deletion_at"?,
  "first_paired_at"?}`. Built field by field from the user row — the row
  also carries the password hash, the ad-attribution columns and the trust
  pin, none of which is exposed.

Shared fixtures: `tests/protocol_fixtures/dto/central/FirstAgentPairedPayload.json`
and `MeResponse.json` (registered on iOS in `FixtureRegistry.swift`; on web the
payload is `firstAgentPairedSchema` and `MeResponse` is deferred to the
generated OpenAPI types). No unknown-key cases — Swift's re-encode drops them.

## Once per account, on a device

Each app owns one component (`AgentPairedConversion`, Android
`data/analytics/`, iOS `Data/Analytics/`) fed by two triggers:

1. the live `account.first_agent_paired` event — post-install by
   construction, so it logs iff no outcome is stored for the user id;
2. every user-events socket **connect** (launch, sign-in, reconnect) — a
   `reconcile()` that skips when an outcome is stored, otherwise calls
   `GET /api/auth/me` and logs iff `first_paired_at` is set and not older than
   `armedAt − 1 h`.

The report's "pairing Success watcher" trigger was dropped on purpose: the
claim runs while the user-events socket is up, the Success verdict is the
agent-online moment 0–60 s later (or never), and `first_paired_at` is stamped
at claim time — the two triggers above cover every case, including an app
that was closed while the pairing happened on the web.

Rules the component enforces, identical on both platforms:

| Rule | Why |
|---|---|
| **Sink disabled ⇒ return before any I/O.** No `/me` call, no arming, no outcome. | Worktree, CI and dev builds have no Firebase config; they must stay silent, and a later configured build must evaluate fresh. |
| `armedAt` is a set-if-absent device timestamp written at the first launch of a Firebase-capable build (Android `MainActivity.onCreate`, iOS `AppContainer.start()`) and self-armed inside `reconcile()` as a fallback; it is never moved afterwards. | Accounts that paired *before* this install or upgrade — including every account backfilled on 2026-09-02 — never log; a pairing done elsewhere while the app was closed does log on the next connect. |
| Outcomes are strings: `logged` or `skipped_predates_install`. A null or unparseable stamp persists nothing (retry next connect). | `first_paired_at` never changes, so "predates install" is final and old accounts stop hitting `/me`. Two states are distinguishable in a bug report. |
| Log first, persist second, inside one lock (Android `Mutex`; iOS one actor turn with no `await` between the check and the write, re-checked after the `/me` await). | The SDK's `logEvent` is a local enqueue that never throws; a duplicate is cheaper than a lost conversion, and the live event and the connect reconcile can race by milliseconds. |
| Outcomes (keyed `agent_paired_logged::<userId>`) and the arming time live in a **per-install** store that platform backups and device transfers never carry — Android: the `ads_conversion` DataStore, excluded in both backup-rule files like the device identity; iOS: a JSON file under Application Support flagged `isExcludedFromBackup` — and are **not** purged by the sign-out sweeps. | Sign-out then sign-in on the same account must not re-log. An arming time restored from an older install would let a pairing made in between pass the gate on the new install and count as a fresh install conversion; per-install state makes the gate mean what it says, and a reinstall arms afresh so an earlier pairing is skipped rather than re-logged. |
| 1 h tolerance, only on the reconcile path. | Covers device-clock skew against the server stamp; widening it would mostly admit web pairings that preceded the install, which are the website lane's conversions. The server stamp may omit fractional seconds when the microsecond is zero, so iOS parses with two `ISO8601DateFormatter`s. |
| Never `setUserId` / user properties; automatic screen reporting off; SSAID (Android) and IDFV (iOS) collection off. | The privacy policy promises the events are keyed by the Firebase app-instance id only. |

Accepted: a second device installed *before* the pairing logs too — Google
Ads attributes the event only to that device's own ad-driven install, so a
duplicate without an ad click is inert. Set the Google
Ads conversion action's counting to **"One"** per click (Stage 6) so the
remaining duplicates collapse.

## Consent defaults and data minimisation

No in-app consent UI, no App Tracking Transparency prompt. The defaults are
build-time constants and mirror the web tag's stance (ad user data granted /
personalisation denied):

| Setting | Android `<meta-data>` (`AndroidManifest.xml`) | iOS `Info.plist` | Value |
|---|---|---|---|
| analytics storage | `google_analytics_default_allow_analytics_storage` | `GOOGLE_ANALYTICS_DEFAULT_ALLOW_ANALYTICS_STORAGE` | granted |
| ad storage | `google_analytics_default_allow_ad_storage` | `GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_STORAGE` | granted |
| ad user data | `google_analytics_default_allow_ad_user_data` | `GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_USER_DATA` | `eu_consent_policy` — denied only for users in regions under Google's EU User Consent Policy (EEA, UK, Switzerland), granted elsewhere |
| ad personalisation | `google_analytics_default_allow_ad_personalization_signals` | `GOOGLE_ANALYTICS_DEFAULT_ALLOW_AD_PERSONALIZATION_SIGNALS` | denied everywhere |
| automatic screen reporting | `google_analytics_automatic_screen_reporting_enabled` | `FirebaseAutomaticScreenReportingEnabled` | off |
| device identifier | `google_analytics_ssaid_collection_enabled` (SSAID) | `GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED` (IDFV) | off |
| app-delegate swizzling | — | `FirebaseAppDelegateProxyEnabled` | off — the app has its own APNs delegate |

Consequences to keep in mind: EEA/UK/CH app installs are **unmeasured by
design** (the same undercount Phase 1 accepted for unconsented web visitors);
Google Analytics still receives app-instance-level usage events there. The
Android Advertising ID is the one identifier the ads attribution needs and is
left on; a user who deletes it in Android settings yields an all-zero id and
attributes nothing.

## The two untracked config files

`android/app/google-services.json` and `ios/Mobaide/GoogleService-Info.plist`
are gitignored (PR #705) and live only in the developer's main checkout —
never in a worktree, never in CI. Everything is built to tolerate their
absence and to refuse to *release* without them:

- **Android** — `app/build.gradle.kts` applies `com.google.gms.google-services`
  only when `app/google-services.json` exists, and a
  `gradle.taskGraph.whenReady` guard fails `bundleRelease` / `assembleRelease`
  before compilation when it is missing. Without the file the Firebase SDK
  initialises nothing (a logged warning, no crash) and
  `FirebaseAnalyticsSink.isEnabled` is false. The release workflow
  (`.github/workflows/release-android.yml`) materialises the file from the
  **`GOOGLE_SERVICES_JSON_BASE64`** repository secret (`base64 <
  android/app/google-services.json`) and fails the job with a clear message
  when the secret is absent — a release without analytics is a silent
  measurement outage, not a degraded build.
- **iOS** — `ios/Mobaide` is a synchronized folder group, so the plist joins
  the bundle automatically when present and no project edit references it.
  `MobaideApp.init()` configures Firebase only when
  `FirebaseOptions(contentsOfFile:)` finds the plist; `ios-build.yml` (push to
  `main`, iPhone 16 simulator, `-onlyUsePackageVersionsFromResolvedFile`) runs
  the whole test bundle with no plist. A Release-only run-script phase
  ("Require Firebase config for Release", first in the app target's build
  phases) fails the build when the plist is missing, so an App Store archive
  made from a worktree cannot ship with analytics inert; Debug and CI builds
  are unaffected.
- **Worktrees** — copy both files in from the main checkout to build with
  analytics (`cp ../../android/app/google-services.json android/app/` and
  the iOS equivalent); they stay untracked.

## Android

- Versions: Firebase BoM `34.18.0` (`firebase-analytics`, no Kotlin extension
  artifact needed), `google-services` plugin `4.5.0`, both in
  `gradle/libs.versions.toml`.
- Manifest: `com.google.android.gms.permission.AD_ID` is declared explicitly
  (the SDK merges it anyway; declaring it keeps the Data Safety answer
  auditable) plus the consent `<meta-data>` above. The SDK merges four more
  permissions into the build — `ACCESS_ADSERVICES_AD_ID`,
  `ACCESS_ADSERVICES_ATTRIBUTION`, `WAKE_LOCK` and
  `com.google.android.finsky.permission.BIND_GET_INSTALL_REFERRER_SERVICE`
  (`ACCESS_NETWORK_STATE` was already merged before Firebase). The privacy
  policy's Section 4 lists the whole merged set, so re-check the merged
  manifest under `app/build/intermediates/` whenever the BoM moves.
- Code: `data/analytics/AnalyticsSink.kt` (interface, `FirebaseAnalyticsSink`
  with `isEnabled` computed from `FirebaseApp.getApps(context)`,
  `NoOpAnalyticsSink`), `data/analytics/AgentPairedConversion.kt`,
  `data/analytics/ConversionStateStore.kt` (own `ads_conversion` DataStore,
  excluded from backup and transfer), `di/AnalyticsModule.kt`;
  `CentralApi.getMe`; the
  `UserEventBus` branch and connect reconcile; `MainActivity.onCreate` arms
  at launch (`armIfNeeded()`), before any sign-in, so an account that pairs
  elsewhere between the install and its first connect still counts. The
  `Application` class is deliberately not the arming site: injecting the
  conversion there pulled the whole Hilt graph (down to the Keystore-backed
  `TokenStore`) into every Robolectric test.
- Debugging: `adb shell setprop debug.firebase.analytics.app com.mobaide.app`
  then watch Firebase console → Analytics → DebugView; `logcat -s FA FA-SVC`
  shows the SDK's own decisions (consent, missing config).

## iOS

- Package: `https://github.com/firebase/firebase-ios-sdk` (up to next major
  from `12.18.0`, Xcode ≥ 26.2), product **`FirebaseAnalytics` only** —
  `FirebaseAnalyticsIdentitySupport` is the IDFA opt-in and must never be
  added; no `AdSupport` link, no `NSUserTrackingUsageDescription`
  (`otool -L` on the built app shows neither AdSupport nor
  AppTrackingTransparency). `OTHER_LDFLAGS` carries `-ObjC` for the Analytics
  binary. Firebase 12 also links Google's on-device conversion measurement
  package (`google-ads-on-device-conversion-ios-sdk`); its "event data"
  variant matches de-identified, temporary event data on the device and is
  left at its default (opt out with
  `GOOGLE_ADS_ON_DEVICE_CONVERSION_EVENT_DATA_ENABLED = NO` if that ever
  changes — and update privacy policy §3.8 with it). Firebase's Analytics
  xcframeworks ship no privacy manifest of their own (only `FirebaseCore`
  does), so the app manifest is the sole declaration.
  `Package.resolved` is committed — CI resolves from it only.
- `Support/Info.plist`: `SKAdNetworkItems` with Google's
  `cstr6suwn9.skadnetwork` (iOS App campaigns attribute through SKAdNetwork;
  there is no per-advertiser id), the consent keys above,
  `FirebaseAutomaticScreenReportingEnabled = NO`,
  `FirebaseAppDelegateProxyEnabled = NO`, `GOOGLE_ANALYTICS_IDFV_COLLECTION_ENABLED = NO`.
- `PrivacyInfo.xcprivacy`: `NSPrivacyTracking` stays `false`; collected types
  are Email Address and User ID (linked, App Functionality), Device ID
  (linked; App Functionality + Analytics + Developer's Advertising),
  Product Interaction (not linked; Analytics + Developer's Advertising) and
  Coarse Location (not linked; Analytics — Google derives it from the IP
  address). `MobaideTests/PrivacyManifestTests.swift` pins it;
  `FirebaseConfigurationTests.swift` pins the Info.plist keys. Firebase's own
  xcframeworks ship their manifests and Apple aggregates them into the privacy
  report, so the app manifest must not contradict them.
- Code: `Data/Analytics/AnalyticsSink.swift`, `Data/Analytics/AgentPairedConversion.swift`
  (an actor), the `FirstAgentPairedApplying` hook in `EventBusHooks.swift`,
  `UserEventBus` demux/route/connect-reconcile, `CentralAPI.me(userToken:)`,
  `Data/Analytics/ConversionStateStore.swift` (backup-excluded JSON file under
  Application Support), `AppContainer` wiring (`start()` arms before the
  user-events bus starts), guarded configure in `MobaideApp.init()`.
- Debugging: launch with the `-FIRDebugEnabled` argument (Xcode scheme or
  `xcrun simctl launch … -FIRDebugEnabled`) and watch DebugView;
  `-FIRAnalyticsDebugEnabled` adds the SDK's verbose log.

## Verifying end to end

Use `tests/e2e/manual_stack.py --fresh` (docs/manual-e2e-testing.md) with the
config files copied into the worktree and DebugView enabled on the device:

1. Register a fresh account inside the app, pair the stack's agent.
   DebugView shows `first_open` at launch and exactly one `agent_paired`
   (`source=live`) at the claim; the relay log shows
   `pairing.first_agent_paired`; the web client's devtools show the
   `account.first_agent_paired` frame.
2. Sign out, sign in again, pair a second agent → no second `agent_paired`.
3. Register on the web client while the app is closed, pair there, then open
   the app → one `agent_paired` (`source=reconcile`) on connect;
   `GET /api/auth/me` returns the stamp.
4. Sign in with an account that paired before the app was installed → nothing
   logged, and `/me` is not called again on later connects.

## Stage 6 runbook (console wiring — YOU)

1. Add the `GOOGLE_SERVICES_JSON_BASE64` repository secret before tagging an
   `android-v*` release.
2. GA4 property (Firebase console → Analytics settings): data retention
   **2 months** for event-level data (the privacy policy states it), Google
   Signals **off**.
3. After the first released builds send events: Firebase console → Analytics
   → Events → mark `agent_paired` as a **key event**.
4. Google Ads → Goals → Conversions → **+ New → App → Google Analytics 4
   (Firebase)** → import `agent_paired` for the Android app and for the iOS
   app; counting **One**; add both actions to the existing **"Agent paired"
   custom goal** and mark them Primary; point App campaigns at the goal.
   Keep the bidding caution from Stage 4: no tCPA until ~15–30 conversions in
   30 days.
5. Update the Play Data Safety form and the App Store privacy details with
   the answers below **with** the store submissions; check the published
   store-listing texts (30 locales) for any surviving "no analytics / no
   tracking" claim and remove it.
6. The privacy policy's Section 13 promises notice of material changes;
   decide whether the release notes plus the updated policy suffice or an
   email goes out.

## Store disclosures

### Google Play — Data safety form

Overview answers: collects or shares required data types **Yes**; all
collected data encrypted in transit **Yes**; users can request deletion
**Yes** (`https://mobaide.com/portal/delete-account-info`, unchanged); app
contains ads **No**; the app requests the advertising ID (the form asks once
`AD_ID` is present) **Yes — for advertising or marketing measurement**.

| Category → type | Collected | Shared | Required / optional | Purposes | Notes |
|---|---|---|---|---|---|
| Personal info → Email address | Yes | No | Required | Account management | Pre-existing (sign-in). |
| Personal info → User IDs | Yes | No | Required | Account management | Pre-existing: the Mobaide account id, Google/Apple `sub`. |
| Device or other IDs → Device or other IDs | Yes | **Yes** (Google) | Required | App functionality; **Analytics; Advertising or marketing** | Pre-existing: the app's random device UUID (app functionality). **New:** Firebase app-instance id and the Android Advertising ID, shared with Google for install attribution. |
| App activity → App interactions | **Yes** | **Yes** (Google) | Required | Analytics; Advertising or marketing | **New:** app opens, sessions, the `agent_paired` event. No screen tracking. |
| Location → Approximate location | **Yes** | **Yes** (Google) | Required | Analytics | **New:** derived by Google from the IP address of the analytics requests; the app never reads device location. |
| App info and performance → Crash logs / Diagnostics | No | No | — | — | No crash reporter; support diagnostics are user-initiated through the user's own mail client. |
| Messages / Other user content | Carry over | — | — | — | Prompt templates are stored on the relay (app functionality); terminal traffic is relayed in memory only and is ephemeral under Play's definition. Verify the existing answers, they are outside this change. |

### App Store Connect — App Privacy

Data used to **track** you: **none** (no IDFA, no ATT, `NSPrivacyTracking =
false`; SKAdNetwork attribution is not tracking under Apple's definition).

| Section | Data type | Purposes | Notes |
|---|---|---|---|
| Data linked to you | Contact Info → Email Address | App Functionality | Pre-existing. |
| Data linked to you | Identifiers → User ID | App Functionality | Pre-existing. |
| Data linked to you | Identifiers → Device ID | App Functionality; **Analytics; Developer's Advertising or Marketing** | Pre-existing device UUID; **new** Firebase app-instance id (IDFV collection is off). |
| Data linked to you | User Content → Other User Content | App Functionality | Carry over: prompt templates. Verify the existing answer. |
| Data **not** linked to you | Usage Data → Product Interaction | Analytics; Developer's Advertising or Marketing | **New:** app opens, sessions, `agent_paired`. |
| Data **not** linked to you | Location → Coarse Location | Analytics | **New:** derived by Google from the IP address. |
| Diagnostics | — | — | None collected. |

"Developer's Advertising or Marketing" (not "Third-Party Advertising") is the
correct purpose: the data measures our own ads; no ads are shown in the app.

### Release notes

Both stores get their notes at release time from the repo's
`google-play-release-notes` and `app-store-connect-release-notes` skills,
which analyse the whole version range. What this change contributes:

- **Google Play** (a permission change users can see, so the Play skill keeps
  it; ≤ 500 characters for the whole note, plain text; the skill translates
  the final note into the other 29 locales):
  `• Helps us learn which ads bring new users: the app now sends Google an
  anonymous install and first-pairing signal. Nothing from your account or
  your sessions is shared, personalised ads stay off, and you can limit it by
  resetting your Advertising ID in Android settings.`
- **App Store**: nothing. The App Store skill excludes analytics from "What's
  New", and this change alters no screen or behaviour; the App Privacy update
  above is the disclosure. If the release contains nothing else, "Bug fixes
  and improvements" is acceptable under Guideline 2.3.12.
