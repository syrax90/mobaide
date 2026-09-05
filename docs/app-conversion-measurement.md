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
told. The rollout as a whole — both lanes, the stage history, the account
configuration and how to pause, change or remove it — is
[agent-paired-conversion.md](agent-paired-conversion.md). The privacy policy's Section 3.8 (`central_server/legal/privacy.md`) is
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
duplicate without an ad click is inert. The imported Google Ads actions
count **One** per click, so the remaining duplicates collapse.

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
  from `12.18.0`, Xcode ≥ 26.2), product **`FirebaseAnalytics`**. In 12.x that
  product bundles `GoogleAppMeasurementIdentitySupport` (IDFA-capable code)
  and Google's on-device conversion measurement
  (`google-ads-on-device-conversion-ios-sdk`); the IDFA-free product is
  `FirebaseAnalyticsCore`, which would also drop on-device measurement. The
  IDFA code is inert as long as the app never links `AdSupport` and never
  requests tracking permission — Firebase's documented guard — so: no
  `AdSupport` link, no `NSUserTrackingUsageDescription`, and `otool -L` on
  every release archive must show neither AdSupport nor
  AppTrackingTransparency (`strings -a Mobaide | grep -c ASIdentifierManager`
  finds the dormant code; expected, not a leak). `OTHER_LDFLAGS` carries
  `-ObjC` for the Analytics binary. The on-device "event data" variant matches
  de-identified, temporary event data on the device and is active with no
  code call; it is left at its default (opt out with
  `GOOGLE_ADS_ON_DEVICE_CONVERSION_EVENT_DATA_ENABLED = NO` if that ever
  changes — and update privacy policy §3.8 with it). Firebase's Analytics
  xcframeworks ship no privacy manifest of their own (only `FirebaseCore`
  does), so the app manifest is the sole declaration.
  `Package.resolved` is committed — CI resolves from it only.
- App Store upload: Xcode embeds the static Firebase xcframeworks as ~51 KB
  code-less stub dylibs, so the Organizer prints four "Upload Symbols Failed …
  no dSYM" warnings (FirebaseAnalytics, GoogleAppMeasurement,
  GoogleAppMeasurementIdentitySupport, GoogleAdsOnDeviceConversion). Harmless;
  keep "Upload your app's symbols" on — the app's own dSYM is the one that
  matters.
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

## Console wiring (done 2026-09-04/05)

What Stage 6 left configured and the rules for the next change. The
account-level inventory is §6 of
[agent-paired-conversion.md](agent-paired-conversion.md).

- **GA4 property** (Firebase console → Analytics settings): event-level data
  retention 2 months (the privacy policy states it), Google Signals off,
  `agent_paired` a key event. No SKAdNetwork conversion value schema (below).
- **Google Ads import** (Goals → Conversions → Summary → + New conversion
  action → sources page, keep *Conversions on an app* → *Create multiple
  conversion actions from a linked account* → the GA4 property): three rows —
  `agent_paired` for the Android stream and for the iOS stream (category
  Sign-up, Count **One**, the same value as the offline "Agent paired" action,
  **Primary**) and `first_open` for **iOS only** (category Download, no value,
  Count One, Primary in the Download goal). Android installs are already the
  Play action Google created with the Android campaign; a Firebase `first_open`
  for Android would count each install twice.
- **Custom goal "Agent paired"**: the offline action plus the two app
  `agent_paired` actions. Only the web (Performance Max) campaign uses it. App
  campaigns never use conversion goals — they pick actions in Settings →
  Bidding — so the goal edit changed nothing about them.
- **App campaigns** (Android; iOS created 2026-09-05): focus *Install volume*,
  *All users*; install tracking = the Play action (Android) / the imported iOS
  `first_open` (iOS). The `agent_paired` actions report on their own (Campaigns
  table → Segment → Conversions → Conversion action; the row appears with the
  first attributed pairing). **Switch to the pairing focus only with data:** at
  roughly 30 pairings attributed to the campaign in 30 days (Google's bar is
  ≥10 different users a day), Settings → Bidding → focus *In-app actions*,
  tick only that platform's `agent_paired`, untick the target (*Maximize
  conversions for in-app actions*) or set the observed cost per pairing × 1.2
  with a daily budget ≥10–15× it; then leave it 14 days and judge over 30.
  Never a guessed target: with no observed pairings the focus has nothing to
  optimise toward and a target only throttles delivery. Demote the Play install
  action to Secondary (observe only) only once the Android campaign bids on
  in-app actions. *Users likely to perform an in-app action* is in Google's
  docs but not offered in this account; skip it.
- **iOS specifics:** the campaign's Conversions column shows Google's modeled
  conversions — up to 5 days late, click-through and engaged-view only — so
  judge ranges that end ≥5 days ago. The SKAdNetwork conversions report
  (Campaigns → Reports → Goals and conversions) is Apple's separate attribution
  and never reconciles with it. Keep ≤8 iOS install campaigns per app. The
  SKAdNetwork conversion value schema (GA4 → Admin → Data streams → iOS →
  *Configure SKAdNetwork conversion values*) stays unset: Google reads only the
  fine value of postback window 1 (0–2 days after install), wants ~50 installs
  and >10 occurrences of each mapped event a day, and a pairing days after the
  install never reaches it. If it is ever set: `agent_paired` → a fine value in
  window 1, in GA4 only, never a second schema through the Ads API or an
  attribution partner.
- **Expect** the campaign figures well below GA4's: GA4 counts every user who
  pairs, updaters and organic installs included; Ads counts attributed
  pairings inside the conversion window.

## Store disclosures

Submitted with Android 3.6.4 (Play, 2026-09-04) and iOS 1.0.3 (6) (App Privacy
published 2026-09-05). The tables below are the record of what was declared and
must keep matching the shipped build; a change to the collected data goes here,
into `PrivacyInfo.xcprivacy` and into privacy §3.8 together.

### Google Play — Data safety form

Overview answers: collects or shares required data types **Yes**; all
collected data encrypted in transit **Yes**; users can request deletion
**Yes** (`https://mobaide.com/portal/delete-account-info`, unchanged); app
contains ads **No**. The separate **Advertising ID** declaration (App content):
uses advertising ID **Yes**, purposes **Analytics** and **Advertising or
marketing**; leave *Turn off release errors* unticked.

Traps met on 2026-09-04: with `AD_ID` in the manifest and the declaration still
at "No", `release-android.yml` fails at the Play commit; fix the declaration
**and** click *Send changes for review* on Publishing overview before
re-running — saving alone does not clear the check. The release page then
warns that older active artifacts lack `AD_ID`: *Release without permission*
is the right button; *Update declaration* would flip it back to "No" and block
the next upload.

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

Edited under App Store Connect → the app → App Privacy → Data Types → Edit and
**published**; publishing is independent of a version and has no review. The
six types below are `PrivacyInfo.xcprivacy` verbatim — Apple cross-reads the
manifest, so keep them in lockstep. Product Interaction, not Advertising Data:
the app receives nothing about ads (no `NSAdvertisingAttributionReportEndpoint`,
so SKAdNetwork postbacks go Apple → Google; the SDK's ad events fire only with
the Mobile Ads SDK).

Data used to **track** you: **none** (no IDFA, no ATT, `NSPrivacyTracking =
false`; SKAdNetwork attribution is Apple's own channel and on-device
measurement leaves the phone only in de-identified form — neither is tracking
under Apple's definition).

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
