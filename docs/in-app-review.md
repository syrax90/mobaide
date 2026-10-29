# In-app review prompt

Both mobile apps ask the store for a rating at one happy moment: the user is
back on the session list after their third good terminal session. Android uses
the Play In-App Review API (`com.google.android.play:review-ktx`); iOS uses
SwiftUI's `@Environment(\.requestReview)` (StoreKit). The web client has no
store, so it has no mirror.

The store owns the card. It decides whether the card shows (quota, a review
already left, an install not from the store) and never tells the app, so the
app neither asks a question first nor offers a button that calls the API (both
stores forbid it), and a failure is silent.

## Rules

Both platforms apply the same rules and numbers. The two rows marked Android
exist only because of how Play shows its card.

| Rule | Value | Why |
|---|---|---|
| Outing | opens when a terminal is first shown; closes the next time the session list settles on screen | the return to the list is a natural break, not mid-task |
| Healthy time | ≥ 60 s with the terminal visible, the app in the foreground, the socket live and the PTY `running` | real use, not a glance |
| Clean visit | when the terminal is hidden, the user had stopped the session, or it is still live and `running` | ending on "Reconnecting…", a `crashed` PTY or a vanished session is an error |
| Good outing | healthy time reached, every visit clean, and no error reported while it was open (a failed stop, scan stop or agent update, on the list or in the terminal) | "never right after an error" |
| Good outings needed | 3 | |
| Launches needed | ≥ 2 launches: Android counts each fresh `MainActivity` (not a recreation), iOS each process | never on the install's first launch; the Android notification service keeps the process alive after a swipe-away, so a process count would stall there |
| Cooldown | 120 days after a request is handed to the store (Android: `markReviewLaunched()` right before `launchReview`; iOS: when the coordinator bumps `requestCount` for `RootView`'s `requestReview()`); the good-outing count restarts at 0 | below Apple's 3-per-365-days cap and Play's unpublished quota |
| Settle wait | 1 s after the list appears; abandoned when a terminal or a git child screen is on screen again | lets the pop finish; auto-resume into a session skips it |
| Delivery | only while the app can show the card (Android: `MainActivity` resumed; iOS: scene active) | a request nobody can see must not start the cooldown |
| Android re-check | after Play returns the card's `ReviewInfo`, the list must still be in front with no terminal over it, or the card is dropped; no cooldown starts and the count stays, so the next good outing asks again | the round trip is long enough to tap into a session |
| Android update dialog | the flexible-update check skips resumes within 60 s of a card launch | the card's close resumes `MainActivity`, which would stack the update dialog on it |

The agent reports `crashed` for any CLI exit it did not ask for, a normal
`/exit` included. Such a visit counts as unclean: a few good sessions are lost,
but the prompt never follows a real crash.

## State

Three keys in each platform's `PreferencesStore` (Android DataStore
`mobaide_prefs`, iOS `UserDefaults` suite `mobaide_prefs`):
`review_prompt_launch_count`, `review_prompt_good_sessions`,
`review_prompt_last_requested_at` (Android epoch ms, iOS epoch seconds). They are
device-local and not account-scoped: no sign-out sweep touches them, because
the store's quota follows the device's store account, not the Mobaide account.
Device backups carry them, so a restored install keeps its counts and cooldown.
The open outing lives in memory only.

## Where it lives

| Part | Android (`android/app/src/main/java/com/mobaide/app/`) | iOS (`ios/Mobaide/`) |
|---|---|---|
| Constants and pure rules | `data/review/ReviewPromptPolicy.kt` | `Data/Review/ReviewPromptPolicy.swift` |
| Healthy-time span counter | `data/review/HealthyTimeMeter.kt` | `Data/Review/HealthyTimeMeter.swift` |
| Outing, counters, request | `data/review/ReviewPromptCoordinator.kt` (`di/ReviewModule.kt`) | `Data/Review/ReviewPromptCoordinator.swift` (`AppContainer`) |
| Launch count | `MainActivity.onCreate` → `recordLaunch()` | `AppContainer.start()` → `recordLaunch()` |
| Visit report | `TerminalViewModel.markScreenVisible()` / `markScreenHidden()`; `markEndedByUser()` from the sessions-panel stop | `TerminalSessionModel.markVisible()` / `markHidden()`; `markEndedByUser()` from `stopSession` |
| Settle and close | `SessionListViewModel.onScreenResumed()` (cancelled on pause) | `SessionListScreen` `.task` (cancelled on disappear) |
| Error signal | `SessionListViewModel` failure paths and `TerminalScreen` `StopFailed` → `noteError()` | `SessionListScreen` `model.error` and the `stopSession` failure → `noteError()` |
| Store call | `MainActivity` collects `requests` while resumed → `ui/review/InAppReviewManager.kt` (re-checks `isReviewMoment()`, then `markReviewLaunched()`) | `RootView` observes `requestCount` → `requestReview()` |

## Testing

- **iOS**: a Debug build always shows the sheet (Submit is disabled); TestFlight
  never shows it; the App Store build is capped by the system. Two cold launches
  and three outings of a minute each, then a return to the list, show it on the
  simulator.
- **Android**: the card shows only when the device's primary Play account got
  the app from the Play internal test track (quotas are off there; a debug build
  deployed over that install works too); internal app sharing shows it with
  Submit disabled. Any other install, an emulator without Play included,
  silently shows nothing. `FakeReviewManager` fakes only the API result, so it
  suits unit tests, not UI checks.
