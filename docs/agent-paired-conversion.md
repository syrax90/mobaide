# "Agent paired" conversion — the Google Ads measurement rollout

**Status (2026-09-05):** both lanes live. Phase 1 (web lane) verified
2026-09-03; Phase 2 (in-app lane) shipped in Android 3.6.4 and iOS 1.0.3 (6),
imported into Google Ads and wired to the three campaigns (web, Android, iOS)
on 2026-09-05. The App campaigns bid on installs until attributed pairings
exist (switch rule in §6.1).

This is the umbrella document for the whole effort. The component-level mechanics
live elsewhere and are not repeated here:

| Component | Detailed doc |
|---|---|
| Landing-page consent banner, Google Ads tag, the `mobaide_ads` cookie | [website.md](website.md), *The consent-gated Google Ads tag* |
| Web app forwarding of the cookie at sign-up | [web-client.md](web-client.md), *Ad attribution at sign-up* |
| The once-per-account `first_paired_at` marker | [qr-pairing.md](qr-pairing.md), *The first-pairing marker* |
| Server-side upload (Data Manager API), env vars, OAuth helper, log vocabulary | [deployment.md](deployment.md), *Google Ads conversion upload* |
| In-app lane: Firebase Analytics, consent defaults, config files, store disclosures | [app-conversion-measurement.md](app-conversion-measurement.md) |
| User-facing statement of all of the above | `central_server/legal/privacy.md` §3.1, 3.5, 3.8, 4, 5, 6, 8, 10 |

**On this page**

- [1. The idea](#1-the-idea)
- [2. Stages and status](#2-stages-and-status)
- [3. Architecture at a glance](#3-architecture-at-a-glance)
- [4. History — what happened and why it changed](#4-history--what-happened-and-why-it-changed)
- [5. What we learned — keep these](#5-what-we-learned--keep-these)
- [6. Configuration inventory](#6-configuration-inventory)
- [7. Monitoring](#7-monitoring)
- [8. How to pause, change or remove it](#8-how-to-pause-change-or-remove-it)
- [9. Open items](#9-open-items)

## 1. The idea

Google Ads campaigns were optimising for cheap events (a landing-page view, an
app install) while the thing that matters — a user who actually installs and
pairs the Mobaide agent — went unmeasured. The plan (2026-09-01, revised
2026-09-03) makes **every campaign, web and app-install alike, optimise for one
conversion: an account pairs its first agent.**

Google Ads cannot attribute website clicks and app installs through one
mechanism, so the one conversion travels through two lanes and is unified in
Google Ads as one custom goal, **"Agent paired"**, that every campaign bids on:

- **Lane A — web campaigns (Phase 1).** The landing page captures the ad click
  id after cookie consent → the web app forwards it into the account at
  sign-up → when the first pairing lands, the **relay uploads the conversion**
  through Google's Data Manager API, using the click id or, for consented
  sign-ups whose click is gone or expired, a SHA-256 of the account email
  (enhanced conversions for leads). Only the server sees the pairing moment
  reliably — it may happen days later, from another device or from a mobile
  app — and no SDK enters the apps.
- **Lane B — app-install campaigns (Phase 2).** Google Analytics for Firebase in
  both apps logs one `agent_paired` event per account; Google Ads imports it as
  app conversion actions. It is the only attribution path App campaigns
  accept, and it is the only way to see accounts created inside the apps,
  which carry no cookie and no click id.

**The conversion, precisely.** The single race-safe moment meaning "this
account paired its first agent ever" is the compare-and-set
`db.mark_user_first_paired()` inside `_complete_pairing()`
(`central_server/central_server/pairing/router.py`).

- It wins exactly once per account, for QR and device-code pairing, from any client,
  and is never cleared.
- On the win the relay logs `pairing.first_agent_paired`, hands the user id to the
  uploader (Lane A) and broadcasts the `account.first_agent_paired` user event
  (Lane B).
- Every account that already owned an agent when the column arrived (2026-09-02) was
  backfilled as paired and will never convert.

## 2. Stages and status

| Stage | What | Owner | Status |
|---|---|---|---|
| 0.1 | Apply for a Google Ads API developer token (Basic access) | operator | **Dropped** — the Data Manager API needs none (see §4, 2026-09-03) |
| 0.2 | Accept the Customer Data Terms; turn on *Enhanced conversions for leads* | operator | Done — terms found accepted 2026-09-02; the leads row turned on 2026-09-03 after it broke the first uploads |
| 0.3 | Phase 2 go / no-go (Firebase in the apps, privacy-policy rewrite, store forms) | operator | Go — taken implicitly by executing 0.4 and Stage 5 |
| 0.4 | Firebase project with an Android and an iOS app, linked to Google Ads; download the two config files | operator | Done 2026-09-02 |
| 1 | Create the "Agent paired" conversion action and custom goal; demote "Page view" | operator | Done 2026-09-02 (custom goal confirmed in the campaign 2026-09-04) |
| 2 | Phase 1 implementation | code | Done — PR #706 (2026-09-02), PR #707 (2026-09-03) |
| 3 | OAuth client, refresh token, prod env, deploy, end-to-end verification | operator | Done — verified 2026-09-03 |
| 4 | Point the web campaign at the "Agent paired" goal, fix bidding | operator | Done 2026-09-04 (confirm the bidding switch was saved, §9) |
| 5 | Phase 2 implementation (server event + `/me`, Firebase in both apps, policy round 2, console drafts) | code | Done — PR #708 (2026-09-04) |
| 6 | Store releases + disclosures, import `agent_paired` into Google Ads, custom goal, App-campaign wiring, iOS install campaign | operator | Done 2026-09-05 — record in [app-conversion-measurement.md](app-conversion-measurement.md), *Console wiring* |
| 7 | Monitoring | both | Ongoing (§7) |

## 3. Architecture at a glance

**Lane A, in order:**

1. Visitor lands on `mobaide.com` with `gclid` / `wbraid` / `gbraid` and
   presses **Accept** on the cookie banner. `site.js` asks the relay
   (`POST /api/ads/consent`, same-origin only) for the signed `mobaide_ads`
   cookie on `.mobaide.com`: click id, kind, arrival time, consent marker and a
   keyed fingerprint of the visitor's network. **Decline** posts the value to
   `POST /api/ads/consent/withdraw` and expires the cookie.
2. The web app forwards the cookie as the optional `ad_attribution` field of the
   register / Google / Apple sign-up requests.
3. The relay verifies the signature and stores the fields on the new `users`
   row (schema v20, Alembic `0018_ads_attribution`). The click id is kept from
   any network; the *consent* (licence for the hashed-email fallback) counts
   only when the sign-up arrives from the network that accepted the banner.
4. First pairing → `first_paired_at` CAS → background upload through
   `central_server/central_server/ads/` (`events:ingest`, OAuth refresh token,
   `transactionId` digest for dedupe, consent fields). Refused credentials
   defer with the attribution kept; any other final outcome clears the
   account's attribution. A follow-up polls Google's verdict. An hourly purge
   drops attribution older than 90 days.

**Lane B, in order:**

1. `_complete_pairing` broadcasts `account.first_agent_paired` to the account's
   user-events sockets and exposes `first_paired_at` on `GET /api/auth/me`.
2. Each app's `AgentPairedConversion` logs `agent_paired` once per account — from
   the live event, or from a reconcile on every user-events connect that reads
   `/me` — gated on a per-install arming time so pairings that predate the install
   never count (details in [app-conversion-measurement.md](app-conversion-measurement.md)).
3. Firebase → GA4 (key event `agent_paired`) → two imported Google Ads app
   conversion actions (Android, iOS) → the same "Agent paired" custom goal for
   the web campaign; the App campaigns select the action directly in their
   Bidding card.

## 4. History — what happened and why it changed

**2026-08-24 — precondition.** PR #664 put a consent-gated Google Ads tag on
the landing page with a "Page view" conversion. That banner is what later
carries the click id; the tag grants only `ad_storage` + `ad_user_data` and
keeps `ad_personalization` denied, a posture every later piece mirrors.

**2026-09-01 — plan.** The original plan keyed the conversion on the trust-key
pin and uploaded through the Google Ads API (`uploadClickConversions`, developer
token with Basic access, seven env vars).

**2026-09-02 — consoles (Stages 0.4, 0.2, 1).**

- Firebase project created with Google Analytics enabled (mandatory for the Ads
  link), Android + iOS apps registered (`com.mobaide.app`), linked to the
  *campaign* Ads account, not the manager.
- No Firebase web app: Lane A already reports web pairings server-side, and a GA4
  web stream would double-count into the same goal and need consent plumbing the SPA
  lacks.
- The Customer Data Terms were already accepted.
- The "Agent paired" action was created through the 2026 wizard (*Conversions
  offline* tile → category *Sign-up* → data source *Offline → Connect data source
  later* → name, Count One, 90-day click window). The category is a label only; the
  action *type* is immutable, so a wrong pick means a new action.

**2026-09-02 — Stage 2, PR #706.** The trust pin is **not** once-per-account — it
is cleared when an account drops to zero agents — so the trigger became the new,
never-cleared `users.first_paired_at` CAS, with a backfill for existing accounts.
Review reshaped the consent path:

- A cookie written by page script proves nothing (any sibling host such as
  `get.mobaide.com` can plant one), so the relay mints and HMAC-signs it,
  same-origin only.
- The payload carries a network fingerprint that sign-up must match; Decline
  withdraws stored attribution server-side; retention is bounded by the
  server-stamped sign-up time.
- Widest blast radius: **Caddy in `CloudflareOrigin` mode was replacing Cloudflare's
  forwarded client address with the edge address**, which broke the fingerprint and
  per-IP rate limits. Fixed with a `trusted_proxies` block.

Deployed that evening with the uploader unconfigured (`ads.disabled`).

**2026-09-03 — Stage 3 and the API blocker.**

- The developer token showed *Explorer Access*, which does reach production
  accounts.
- **Google closed `uploadClickConversions` on 2026-06-15 to developer tokens
  without prior upload history, with no exception process**, and named the Data
  Manager API as the replacement. The Stage 2 uploader could never succeed, and
  because every terminal failure clears attribution, enabling it would have burned
  every stored conversion.
- PR #707 rewrote the uploader for the Data Manager API the same morning: no
  developer token, no API-version knob, same click ids and hashed emails,
  validate-only smoke test in the OAuth helper. Review added the `transactionId`
  digest (re-sends become duplicates Google drops), the rule that refused
  credentials **defer** instead of clearing, a 200 without a request id as a
  failure, and a transport lock that survives task cancellation.

The cutover then hit three traps, diagnosed from prod logs and validate-only
probes:

1. The helper's output had been saved to a side file and never merged, so prod
   shipped the older `adwords`-scope token
   (`ads.credentials_rejected reason=oauth_refresh_failed`; uploads deferred, not
   lost).
2. A pairing from an already-signed-in browser logged only
   `pairing.claim_completed` because nothing new registered.
3. The hashed-email path failed with a bare `400 INVALID_ARGUMENT` whose hidden
   reason was `DESTINATION_ACCOUNT_NOT_ENABLED_ENHANCED_CONVERSIONS_FOR_LEADS`. The
   separate *Enhanced conversions for leads* row was off and the unified switch had
   not flipped it; two test accounts were burned finding out.

With the row on, a consented web sign-up that paired the Mac agent produced an
upload Google accepted at 15:07 UTC and reported as `SUCCESS` at 15:57 UTC. An
account created in the iOS app paired the same day and logged
`pairing.conversion_skipped reason=no_consent` — correct, and the reason Phase 2
exists.

**2026-09-04 — Stage 4.** The web campaign (Performance Max) was switched to
campaign-specific goals with only the "Agent paired" custom goal, and its bidding
from a page-view-calibrated Target CPA to *Maximize conversions* without a target (a
starved tCPA strangles delivery; revisit at roughly 15–30 conversions in 30 days).
The Android install campaign was left alone: App campaigns cannot use a website
action at all.

**2026-09-04 — Stage 5, PR #708.** Server event + `GET /api/auth/me`, Firebase
Analytics in both apps, web parity mirror, privacy policy §3.8 and the
"no analytics SDK" claims rewritten, `docs/app-conversion-measurement.md`
with the console drafts.

- **Consent posture chosen by the operator:** `ad_user_data = eu_consent_policy`
  (denied only under Google's EU consent policy), personalisation denied
  everywhere, no in-app consent UI, no ATT, no IDFA.
- **Review rounds** moved the arming to app launch (an account that pairs elsewhere
  between install and first sign-in must still count), added a Release-only iOS
  guard mirroring Android's, and moved the conversion state into per-install stores
  excluded from backups (a restored arming time would turn an old pairing into a
  fresh install conversion).
- **GA4 configured:** retention 2 months, Google Signals off, key event
  `agent_paired` created "with code", no default value.
- The relay was redeployed from `main`, so the policy's Firebase text is live ahead
  of the store builds.
- A debug build on a real Android phone against production logged and uploaded
  `agent_paired` on both the live and the reconcile path (`ga_conversion=1`,
  HTTP 204). GA4's Realtime and DebugView did not show the event within the
  session — see §9.

**2026-09-04 — Stage 6, stores.**

- The `android-v3.6.4` release run failed at the Play upload: the manifest now
  carried `AD_ID` while the Advertising ID declaration still said "does not use".
  Declaration set to Yes, Data safety answers entered, both **sent for review**;
  the failed job was then re-run successfully (trap and fix in §5).
- iOS 1.0.3 (6) was archived and uploaded. Inspecting that archive showed the
  `FirebaseAnalytics` product bundles IDFA-capable code and on-device conversion
  measurement (the docs had the product mapping backwards). The shipped posture (no
  AdSupport link, no ATT, on-device measurement active) is exactly what privacy §3.8
  states, so neither code nor policy changed (§5).

**2026-09-05 — Stage 6, consoles.** App Privacy was published in App Store Connect.
In Google Ads the linked GA4 property's key events were imported (`agent_paired` for
the Android and iOS streams, `first_open` for iOS only) and the two app
`agent_paired` actions joined the "Agent paired" custom goal, which now holds three
actions (settings in §6.1).

Both App campaigns stay on *Install volume / All users*:
the in-app-action focus has nothing to learn from until attributed pairings exist,
and a guessed target CPA would only throttle delivery. The iOS install campaign was
created; 3.6.4 is in production on Play.

## 5. What we learned — keep these

**Google Ads account and UI**

- `uploadClickConversions` on the Google Ads API is closed to developer tokens
  without prior upload history (since 2026-06-15,
  `CUSTOMER_NOT_ALLOWLISTED_FOR_THIS_FEATURE`). The Data Manager API is the path; do
  not resurrect the old client.
- *Enhanced conversions for leads* is a **separate row** under Goals →
  Conversions → Settings. The unified "Enhanced conversions" switch does not
  turn it on; its method dropdown (Google tag / GTM) is a label, not a gate.
  Off ⇒ every hashed-email upload fails up front with `400 INVALID_ARGUMENT`
  and the account's attribution is cleared for good.
- The action type is immutable. "Agent paired" must stay an *Import → track
  conversions from clicks* (offline) action; GA4-imported and tag-based actions
  cannot take uploads.
- The click-through window is an ingestion filter, not a reporting setting: a
  conversion later than the window is rejected and cannot be re-uploaded after
  widening. It must equal `GOOGLE_ADS_CLICK_WINDOW_DAYS` (90).
- Only Primary actions feed Smart Bidding; Secondary ones show under "All
  conversions". "Awaiting conversions", "Set up import", a "Misconfigured"
  Page-view goal with zero primary actions and "0 of 1 campaigns" on the
  account-default goals are all expected states, not errors.
- App campaigns accept only app conversion actions (Firebase / attribution
  partner / Google Play) and **never use conversion goals**: they pick actions
  in Settings → Bidding. The custom goal matters only for the web campaign, and
  inside a custom goal Primary vs Secondary does not change bidding. An action in
  both a standard goal and a custom goal is not double counted; ticking only the
  custom goal is what let the app actions join the web campaign without another
  campaign edit.
- In-app-action bidding needs observed pairings (switch rule in §6.1). The existing
  campaign's Bidding card does offer the focus dropdown, so the switch can be made in
  place; *Users likely to perform an in-app action* is not offered in this account.
- iOS App-campaign figures are Google's modeled conversions: up to 5 days late. The
  SKAdNetwork conversions report is Apple's separate attribution and never
  reconciles with them. The SKAdNetwork conversion value schema is left unset (a
  pairing days after the install never reaches it); if ever set, set it in GA4 only.
  Details: [app-conversion-measurement.md](app-conversion-measurement.md), *Console
  wiring*.

**Store consoles** (full record in
[app-conversion-measurement.md](app-conversion-measurement.md), *Store disclosures*)

- Play rejects the bundle at commit time when the manifest carries `AD_ID` but the
  Advertising ID declaration says "No". The fix is the declaration (Yes; Analytics +
  Advertising or marketing) **and** *Send changes for review* on Publishing overview
  — saving alone does not clear the check. Leave *Turn off release errors*
  unticked. The later "an active artifact doesn't include AD_ID" warning is about
  older bundles: *Release without permission*, never *Update declaration*.
- App Store Connect → App Privacy is edited and **published** independently of a
  version, with no review. The answers are the privacy manifest's six data types
  verbatim (Apple cross-reads the manifest): Product Interaction, not Advertising
  Data; purpose *Developer's Advertising or Marketing*, not *Third-Party
  Advertising*; tracking "none" (no IDFA or ATT, SKAdNetwork is Apple's own channel,
  and on-device measurement leaves the phone only in de-identified form).

**Data Manager API**

- Fast-fail (one bad row rejects the request, no partial-failure envelope) and
  asynchronous: a 200 carries only a `requestId`; the verdict arrives via
  `requestStatus:retrieve` 30 minutes to 24 hours later (`SUCCESS` landed in
  about 50 minutes on 2026-09-03). "Accepted" is not "matched".
- A 400's top-level message is generic; the real reason sits in
  `details[].fieldViolations[].reason`, which the relay deliberately never
  reads. To see it, post a validate-only request with the same payload from
  the Mac (the helper's `--verify` exercises the same account-side checks).
- The operating account must be the account that **owns** the conversion
  action; the manager account is only the login account.

**OAuth and Google Cloud**

- The consent screen must be **In production**; a Testing screen issues
  refresh tokens that die after 7 days (`oauth_refresh_failed` a week later,
  uploads deferred). The unverified-app interstitial is passable and
  verification is not required for a single-operator client.
- Re-running the helper does not revoke the previous token, and revoking the
  app at the Google account's permissions page kills **every** token of that
  client. A refresh-token grant from the Mac returns `scope`, the cheap way to
  tell an `adwords` token from a `datamanager` one.
- Replace the three `GOOGLE_ADS_OAUTH_*` lines in the prod env, never append:
  duplicate keys let the old one win.
- python.org Python on macOS ships without a CA bundle until *Install
  Certificates.command* runs; test hand-run scripts with `env -u SSL_CERT_FILE`.

**Relay and deploy**

- Every terminal outcome except a credential failure clears the account's
  attribution: **each account is single-shot**, and a test sign-up spends its
  one shot. Verify with a fresh email sign-up in a signed-out private window;
  never with a long-standing account (backfilled) or a fabricated `gclid`
  (uploads, never matches, may 400).
- `pairing.conversion_status` is written once, at Google's terminal verdict or
  the 24 h deadline; a poll that sees `PROCESSING` logs nothing, and a container
  restart drops the follow-up. The action's Diagnostics tab is the source of
  truth.
- `deploy_central.py` needs `--tls-mode CloudflareOrigin` on every run;
  compose recreates the container on an env change even with `--skip-build`.
- Any index in the runtime `_SCHEMA` that references a column added in
  migration block N breaks older blocks on a pre-N database; column ALTERs go
  in the pre-ladder guard. SQLite `DROP COLUMN` chokes on comment lines before
  the last column — rebuild test tables instead.

**Firebase, GA4 and the apps**

- Firebase iOS 12.18: the plain `FirebaseAnalytics` product **does** bundle
  IDFA-capable code (`GoogleAppMeasurementIdentitySupport`) and on-device
  conversion measurement; the IDFA-free product is `FirebaseAnalyticsCore`.
  - The IDFA code is inert as long as the app never links `AdSupport` and never
    asks for tracking permission. `otool -L` on each release archive is the check
    (`strings -a Mobaide | grep -c ASIdentifierManager` finds the dormant code;
    expected, not a leak).
  - Do not switch to `FirebaseAnalyticsCore` casually: it silently drops the
    event-data on-device conversion measurement that privacy §3.8 describes.
  - The Analytics xcframeworks ship no privacy manifest, so the app's is the sole
    declaration. Requires `-ObjC`.
- The four "Upload Symbols Failed … no dSYM" warnings for the Firebase frameworks
  at App Store upload are harmless (code-less stub dylibs); keep "Upload your app's
  symbols" on.
- Android uploads are performed by Google Play services, not the app process:
  a DNS filter or VPN on a test phone that null-routes `app-measurement.com`
  silently blocks every upload (`FA-SVC` logs `ERR_CONNECTION_REFUSED`), and
  app-level whitelists do not help.
- GA4's "Create an event" dialog: use *Create with code* (registers a name);
  *Create without code* derives a synthetic event from a rule. Decline the default
  key-event value; the value belongs on the Ads action. Realtime and DebugView are
  best-effort and drop backgrounded devices; the Events / Key events reports lag
  24–48 h.
- The downloaded `google-services.json` may carry a legacy
  `IS_ANALYTICS_ENABLED=false`; it does not mean GA4 is off.

## 6. Configuration inventory

No secret or account-specific value is recorded here; §6.7 says where each one
lives.

### 6.1 Google Ads (campaign account, reached through a manager account)

| Item | Setting |
|---|---|
| Conversion action **"Agent paired"** | Import / offline (track conversions from clicks), category Sign-up, Count **One**, click-through window **90 days**, a per-conversion value (operator's estimate of a paired user), **Primary** |
| "Page view" action (landing tag) | Secondary — its account-default goal shows zero primary actions by design |
| Imported app actions `agent_paired` (Android) and `agent_paired` (iOS) | From the linked GA4 property (Firebase), imported 2026-09-05; category Sign-up, Count **One**, the same per-conversion value as the offline action, **Primary**; members of the "Agent paired" custom goal |
| Imported app action `first_open` (iOS) | Category Download, no value, Count One, Primary in the Download goal; **not** in the custom goal — it is the iOS campaign's install action. No Android counterpart (the Play action covers Android installs) |
| Android install action (Play) | Primary; demote to Secondary (observe only) only once the Android campaign bids on in-app actions, so its Conversions column reports pairings only |
| Custom goal **"Agent paired"** | Three actions: the offline action plus the two app `agent_paired` actions. Used by the web campaign only — App campaigns pick actions in their Bidding card |
| Web campaign (Performance Max) | Campaign-specific goals: only the "Agent paired" custom goal; bidding *Maximize conversions* with no target until ~15–30 conversions / 30 days |
| Android App campaign | Focus *Install volume*, *All users*, install tracking = the Play action, a target cost per install. **Switch rule:** see below the table |
| iOS App campaign (created 2026-09-05) | App promotion / App installs / iOS, same shape as Android: focus *Install volume*, *All users*, install tracking = the imported iOS `first_open`; same switch rule with `agent_paired` (iOS); iOS bids run ~1.5× Android; keep ≤8 iOS install campaigns; no SKAdNetwork conversion value schema |
| Settings → Customer data terms | Accepted |
| Settings → Enhanced conversions (unified) | On, method label "Google Ads API" |
| Settings → **Enhanced conversions for leads** | **On**, method label "Google tag" (label only) — required for the hashed-email path |
| Google Ads API developer token | Exists (Explorer Access), **unused** since PR #707 |

**Switch rule (App campaigns, from installs to in-app actions).** Trigger: ~30
pairings attributed to the campaign in 30 days (Google's bar: ≥10 users/day). Then:

1. Go to Settings → Bidding → focus *In-app actions*.
2. Tick only `agent_paired` (Android).
3. Either leave the target unticked (*Maximize conversions for in-app actions*), or
   set it to the observed cost per pairing × 1.2 with a daily budget ≥10–15× it.
4. Make no edits for 14 days, then judge over 30.

The iOS campaign uses the same rule with `agent_paired` (iOS).

### 6.2 Google Cloud

| Item | Setting |
|---|---|
| Project | The OAuth client lives in the project that owns the Google Sign-In clients. The Firebase project and its GA4 property are the separate auto-named `gen-lang-client-…` project — the prefix Google Ads prints in front of the app streams. Nothing requires them to be the same |
| APIs | **Data Manager API** enabled (Google Ads API also enabled, unused) |
| OAuth consent screen | External, **In production**, unverified app (interstitial accepted by the operator); privacy URL `https://mobaide.com/privacy.html`, authorized domain `mobaide.com`, no logo, no terms link |
| OAuth client | Type **Desktop app**; used only by `deploy/prod/google_ads_oauth.py` and the relay |
| Scope granted | `https://www.googleapis.com/auth/datamanager` (the earlier `adwords` grant is dead weight) |

### 6.3 Central server (production)

| Variable (in `/opt/mobaide/.env`) | Role |
|---|---|
| `GOOGLE_ADS_CUSTOMER_ID` | The account that owns the action (digits) |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | The manager account — set, because access is via a manager |
| `GOOGLE_ADS_OAUTH_CLIENT_ID`, `GOOGLE_ADS_OAUTH_CLIENT_SECRET` | The Desktop-app client |
| `GOOGLE_ADS_OAUTH_REFRESH_TOKEN` | Minted by the helper for the `datamanager` scope |
| `GOOGLE_ADS_CONVERSION_ACTION_ID` | The numeric id of "Agent paired" (`ctId=` in its URL) |
| `GOOGLE_ADS_CLICK_WINDOW_DAYS` | Unset (default 90 = the action's window) |
| `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_API_VERSION` | Retired; a leftover only logs `ads.obsolete_setting` |

Also load-bearing:

- `JWT_SECRET_KEY` (derives the cookie HMAC key);
- `USER_SESSION_COOKIE_SECURE` (the cookie's `Secure` flag);
- the `trusted_proxies` block in `deploy/prod/Caddyfile.CloudflareOrigin`;
- schema `user_version = 20` with the six `users` columns (`ad_click_id`,
  `ad_click_kind`, `ad_click_at`, `ads_consent`, `ad_attribution_sig`,
  `first_paired_at`);
- Alembic `0018_ads_attribution`.

Deploy: `deploy_central.py --tls-mode CloudflareOrigin --skip-build` after an env
change. A healthy start logs `ads.enabled api=data_manager …` and nothing else from
`ads.`. Currently deployed: `main` at the PR #708 merge.

### 6.4 Firebase and GA4

| Item | Setting |
|---|---|
| Firebase project | One project, Google Analytics enabled; apps: Android (`com.mobaide.app`) and iOS (`com.mobaide.app`, its record must carry the App Store ID and Team ID — SKAdNetwork attribution needs both); no web app |
| Integrations → Google Ads | Linked to the campaign account (this is what makes the app streams appear in Ads' conversion picker) |
| GA4 property | Event-level data retention **2 months** (stated in the privacy policy); Google Signals **off**; key event `agent_paired` (created "with code", no default value, once per event) |
| Config files | `android/app/google-services.json`, `ios/Mobaide/GoogleService-Info.plist` — gitignored, only in the main checkout, re-downloadable from Project settings → Your apps |
| Release key folder | `android/google-services.json` in the local key folder — required by `deploy/prod/release_android.py` (see [versioning-and-updates.md](versioning-and-updates.md#local-key-folder)) |

### 6.5 Apps

Mechanics, versions and the consent-defaults table live in
[app-conversion-measurement.md](app-conversion-measurement.md). In short:

- Android: Firebase BoM `34.18.0`, `google-services` plugin `4.5.0`, applied only
  when the JSON exists; release builds refuse to run without it; `AD_ID` declared.
- iOS: `firebase-ios-sdk` from `12.18.0`, product `FirebaseAnalytics` (bundles
  dormant IDFA code — never link `AdSupport`, §5), `-ObjC`, `SKAdNetworkItems`,
  updated `PrivacyInfo.xcprivacy`; a Release-only run-script phase refuses to build
  without the plist.
- Consent defaults on both: analytics and ad storage granted, ad user data
  `eu_consent_policy`, ad personalisation denied, screen reporting / SSAID /
  IDFV off, no user id or user properties.
- Conversion state: per-install, backup-excluded, never purged on sign-out.

### 6.6 Privacy policy and store disclosures

- `central_server/legal/privacy.md` (last updated 2026-09-04): §3.1 click
  attribution row, §3.5 banner + `mobaide_ads` cookie, §3.8 in-app
  measurement, §4 SDK-merged permissions, §5 legal bases (consent for the web
  lane, legitimate interest for the app lane), §6 Google as recipient, §8
  retention (90 days / 2 months), §10 withdrawal and objection. Pinned at
  exactly 14 H2 sections and 7 tables by `test_privacy.py`.
- Play Data Safety, the Advertising ID declaration (Yes; Analytics + Advertising or
  marketing) and the App Store App Privacy answers (the privacy manifest's six
  types, tracking none) were **submitted** with Android 3.6.4 and iOS 1.0.3 (6) on
  2026-09-04/05. The tables in
  [app-conversion-measurement.md](app-conversion-measurement.md) are the record of
  what was declared and must keep matching the shipped build.

### 6.7 Where the identifiers and secrets live

| Kind | Where | Recoverable? |
|---|---|---|
| Ads customer id, manager id, conversion action id | Prod env; the operator's local env file outside the repo (mode 600) | Yes — visible in the Google Ads UI |
| OAuth client id / secret | Google Cloud Console → Clients; prod env; local env file | Yes (secret can be reset in the console) |
| OAuth refresh token | Prod env; local env file | Re-mint with the helper; never committed |
| Firebase config files | Main checkout only (gitignored); Firebase console | Re-download any time |
| Release copy of the Android JSON | Local key folder `android/google-services.json` (mode 600) | Copy again from the main checkout or the Firebase console |
| GA4 property id, Firebase project id | Firebase / GA4 console | Yes |
| Google Ads developer token | Google Ads → API Center; local env file | Unused |

## 7. Monitoring

No polling is needed; the relay logs every outcome once. Run this check:

- after a deploy or env change;
- when Google Ads shows nothing a day after a campaign click.

Otherwise glance at the action's Diagnostics tab weekly. The check:

```bash
ssh mobaide "cd /opt/mobaide && docker compose -f docker-compose.prod.yml logs --since 24h --no-log-prefix central-server \
  | grep -E 'ads\.|pairing\.(first_agent_paired|conversion_)'"
```

| Line | Meaning | Action |
|---|---|---|
| `ads.enabled api=data_manager …` | Uploader configured at startup; the only `ads.` line a healthy start prints | None |
| `pairing.first_agent_paired` | The conversion moment fired for this account | Expect one of the next four lines within seconds |
| `pairing.conversion_uploaded … request_id=…` | Google accepted the request; not yet a match | None; the request id is what Google support asks for |
| `pairing.conversion_status … status=SUCCESS records=1` | Google finished processing; written once, 45 min to 24 h later; absent after a restart | `FAILED` or `errors=` names a reason enum — look it up in Diagnostics |
| `pairing.conversion_skipped reason=no_consent` | Normal for accounts created in the apps or without a consented web sign-up | None (Lane B measures those) |
| `pairing.conversion_upload_failed … reason=http_4xx` | Google rejected the event; the account's attribution is cleared | Probe with a validate-only upload from the Mac to read the hidden reason; fix the account setting |
| `ads.credentials_rejected` then `pairing.conversion_upload_deferred` | Token, scope, API enablement or account access refused; nothing lost | Re-mint or fix the env, recreate the container; `ads.resume_pending` sends the backlog |

**Lane B checks:**

- Firebase console → Analytics → Events / Key events (`agent_paired`, 24–48 h lag)
  and DebugView with a debug-flagged device.
- In Google Ads: Goals → Conversions → Summary. Each imported action moves from
  "Awaiting conversions" to "Recording conversions" about a day after GA4 sees the
  event.
- The Campaigns table segmented by *Conversion action* shows pairings attributed per
  campaign (a row appears only once an action has a conversion).
- Expect the campaign figure well below GA4's (GA4 counts every user who pairs,
  organic installs included). iOS figures are modeled and up to 5 days late: judge
  ranges that end ≥5 days ago, over 30 days.
- On-device: Android `adb logcat -s FA FA-SVC` after `setprop debug.firebase.analytics.app
  com.mobaide.app`; iOS `-FIRDebugEnabled`.

## 8. How to pause, change or remove it

### 8.1 Pause quickly (nothing is lost)

- **Stop spending on the goal:** pause the campaign, or Campaign → Settings →
  Goals → *Use account default goals*. Nothing else needs to change.
- **Stop web-lane uploads:** remove (or comment out) any one required
  `GOOGLE_ADS_*` line in `/opt/mobaide/.env` and run
  `deploy_central.py --tls-mode CloudflareOrigin --skip-build`.
  - Startup logs `ads.disabled reason=not_configured` (or
    `ads.misconfigured missing=…`).
  - The cookie, the sign-up forwarding, the columns, the CAS and the 90-day purge
    keep running inertly.
  - Re-enabling within 90 days re-uploads every account still pending
    (`ads.resume_pending`); accounts with a terminal outcome are gone.
- **Stop app-lane spend or counting:**
  - Pause the iOS campaign; in the Android campaign keep (or revert to) *Install
    volume* in the Bidding card — nothing else there references the app actions.
  - To keep app pairings out of the web campaign's goal, open the "Agent paired"
    custom goal and untick the two app actions.
  - In GA4, Admin → Key events lets `agent_paired` be un-marked.
  - A shipped app keeps sending until a new build changes it (§8.4).

### 8.2 Change something

- **Rotate or fix credentials:** re-run
  `deploy/prod/google_ads_oauth.py --client-id … --verify --customer-id …
  --conversion-action-id … --login-customer-id …`, **replace** the three
  `GOOGLE_ADS_OAUTH_*` lines (delete the old ones first), recreate the
  container. Credential failures defer, so the backlog survives within 90
  days. Do not revoke the app on the Google account's permissions page unless
  every token of that client is meant to die.
- **New conversion action or account:** create the action (offline / import
  type, Count One, 90 days), put its id in `GOOGLE_ADS_CONVERSION_ACTION_ID`;
  `GOOGLE_ADS_CUSTOMER_ID` must be the account that owns it. Add it to the
  custom goal and re-point campaigns. Run `--verify` before deploying.
- **Change the click-through window:** change the action and
  `GOOGLE_ADS_CLICK_WINDOW_DAYS` together (1..90; the policy promises 90-day
  retention, so nothing wider).
- **Change the conversion value:** editable on the action, forward-only.
  Keep the Phase 2 app actions on the same value.
- **Touch the Google Ads settings page:** turn the uploader **off first** (§8.1)
  before unticking *Enhanced conversions for leads* or the Customer Data
  Terms — with them off, every hashed-email upload 400s and burns the
  account.
- **Change the in-app consent posture:** the four consent keys in
  `AndroidManifest.xml` / `Info.plist` are build-time constants.
  - Edit them in lockstep with privacy §3.8 and ship a release.
  - Opting iOS out of on-device conversion measurement is
    `GOOGLE_ADS_ON_DEVICE_CONVERSION_EVENT_DATA_ENABLED = NO` plus the same policy
    edit.
  - Enabling ad personalisation anywhere reopens the `www.google.<tld>` CSP trap on
    the landing page and the policy's promise.
- **Move to a target (web) or to in-app-action bidding (apps):** only after
  ~15–30 conversions in 30 days, from the observed cost per pairing (× 1.2 for
  a target), never from a guess or the old page-view target. App campaigns
  follow the §6.1 switch rule.

### 8.3 Remove the web lane entirely

These pieces are load-bearing for each other and go together (the list in
[website.md](website.md) is authoritative):

- the landing banner and the `site.js` consent module;
- the `_LANDING_CSP` bucket;
- the `mobaide_ads` issuance (`ads/router.py`, `ads/consent.py`);
- `web/src/auth/adAttribution.ts` and the `ad_attribution` request field
  (regenerate the OpenAPI snapshot);
- the `ads/` uploader package;
- the `app.py` wiring and purge loop;
- the privacy passages in §3.1, 3.5, 5, 6, 8 and 10.

Database and Google side:

- The `users` columns can stay (they are nullable and ignored) or be dropped with
  Alembic `0018`'s `downgrade()` on a one-off basis — the runtime ladder only ever
  adds.
- `first_paired_at` should stay: Lane B reads it.
- Google side: demote or remove the action, delete the custom goal, re-point
  campaigns; delete the Desktop OAuth client if nothing else uses it.

### 8.4 Remove the in-app lane entirely

Revert the `feat(android)` / `feat(ios)` commits of PR #708 and their four
review fixes, or at minimum do the following.

**Code and build:**

- Drop the Firebase dependency and the `google-services` plugin block (with its
  release guard) on Android.
- Drop the `FirebaseAnalytics` package and the Release run-script phase on iOS.
- Drop the `AD_ID` declaration and consent `<meta-data>` / plist keys.
- Drop the `data/analytics` / `Data/Analytics` components and their bus hooks.
- Drop the `google-services.json` copy step in `deploy/prod/release_android.py`.

**Then:**

- Rewrite privacy §3.8, §4 (the SDK-merged permissions disappear), §5, §6, §8, §10
  and §12.
- Roll back the Play Data Safety answers, the Advertising ID declaration and the
  App Store App Privacy answers with the next submissions.
- Un-mark the GA4 key event, take the two app `agent_paired` actions out of the
  custom goal, pause or delete the iOS campaign (its install action is the imported
  `first_open`) and delete the three imported actions.
- Unlink Firebase from Google Ads (Project settings → Integrations) if nothing else
  needs it.

The server's broadcast and `GET /api/auth/me` are additive and may stay.

### 8.5 What persists and what is irreversible

- `users.first_paired_at` is never cleared, and the 2026-09-02 backfill stays:
  an account converts at most once, ever, and pre-existing accounts never do.
- Attribution cleared by a terminal rejection cannot be recovered; each
  account is single-shot.
- Conversions and events already sent to Google stay there under Google's
  retention, including for deleted Mobaide accounts (the upload carries no
  account id, only the click id or email hash).
- Customer Data Terms acceptance is account state with no undo path.
- On-device conversion state is per-install; a reinstall or cleared app data
  re-arms it (an old pairing is then *skipped*, not re-logged).

## 9. Open items

1. Confirm the Stage 4 bidding change (Target CPA → Maximize conversions) was
   saved and the web campaign enabled.
2. First production `agent_paired`: the GA4 Events report shows it and the two
   imported actions reach "Recording conversions". The 2026-09-04 Android test
   events were never confirmed in GA4 (Realtime and DebugView showed nothing;
   the remaining hypothesis is the `source` parameter name).
3. Validate the iOS lane once on a device or simulator with DebugView (only
   Android was validated end to end).
4. The bidding switch on each App campaign per the §6.1 rule, then demote the
   Play install action to Secondary.
5. Privacy §13: record the decision taken (release notes + updated policy, or
   an email to accounts).
6. Clean up:
   - delete the test accounts from 2026-09-03/04;
   - move the Mac agent back to the main account if it is not;
   - undo the test phone's debug property, verbose logging and DNS whitelist entry;
   - once every pre-3.6.4 bundle is retired from all Play tracks, confirm *Turn off
     release errors* is still unticked on the Advertising ID declaration.
7. Hardening candidates, offered and not started:
   - log `fieldViolations[].reason` enums on a 400 (never the description);
   - treat the three `DESTINATION_ACCOUNT_*` reasons as deferrable instead of
     clearing attribution;
   - log a short line per pending status poll;
   - persist the status follow-up across restarts;
   - use the `/me` `Date` header for device clock skew;
   - register `source` as an event-scoped GA4 custom dimension if per-source
     breakdowns are wanted.
8. Outcome of the Cloud project's brand verification (Search Console domain
   property) was never confirmed; it blocks nothing.
