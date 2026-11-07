package com.mobaide.app

import android.Manifest
import android.app.UiModeManager
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.mobaide.app.data.analytics.AgentPairedConversion
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.events.UserEventBus
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.ThemeMode
import com.mobaide.app.data.repository.AgentSwitchCoordinator
import com.mobaide.app.data.repository.ServerCompatibility
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.VersionGateRepository
import com.mobaide.app.data.review.ReviewPromptCoordinator
import com.mobaide.app.notifications.SessionEventService
import com.mobaide.app.ui.navigation.AppNavigation
import com.mobaide.app.ui.review.InAppReviewManager
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.update.InAppUpdateManager
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var sessionManager: SessionManager

    @Inject
    lateinit var agentEventBus: AgentEventBus

    // Injected only to exist before agentEventBus.start(): the bus does not
    // replay, so a repository created later misses the connect-time
    // sessions.changed snapshot and shows an empty list until the next change.
    @Inject
    lateinit var sessionRepository: SessionRepository

    @Inject
    lateinit var userEventBus: UserEventBus

    @Inject
    lateinit var versionGate: VersionGateRepository

    @Inject
    lateinit var inAppUpdateManager: InAppUpdateManager

    @Inject
    lateinit var preferencesStore: PreferencesStore

    @Inject
    lateinit var agentPairedConversion: AgentPairedConversion

    @Inject
    lateinit var agentSwitchCoordinator: AgentSwitchCoordinator

    @Inject
    lateinit var reviewPromptCoordinator: ReviewPromptCoordinator

    @Inject
    lateinit var inAppReviewManager: InAppReviewManager

    /** When the review card was last launched, on the monotonic clock; 0 before the first one. */
    private var reviewLaunchedAtMs = 0L

    /** Session id from a tapped notification; consumed by [AppNavigation]. */
    private val deepLinkSessionId = MutableStateFlow<String?>(null)

    private val requestNotificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) {
            // Result intentionally ignored: every post site re-checks the
            // permission, so a denial just means alerts stay silent (no crash).
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        agentEventBus.start()
        userEventBus.start()
        // Arm the conversion window at launch, before any sign-in, so a
        // pairing done elsewhere before this install's first connect still
        // counts (iOS parity: AppContainer.start()).
        lifecycleScope.launch { agentPairedConversion.armIfNeeded() }
        // A recreation (rotation, theme change) is not a launch.
        if (savedInstanceState == null) {
            lifecycleScope.launch { reviewPromptCoordinator.recordLaunch() }
        }
        maybeRequestNotificationPermission()
        consumeSessionExtra(intent)

        // Start the notification foreground service only while this activity is
        // at least STARTED (foreground), so the start is never a background FGS
        // start (disallowed on Android 12+). The service stops itself when
        // auth drops below FullyConnected.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                sessionManager.authState.collect { state ->
                    if (state == SessionManager.AuthState.FullyConnected) {
                        SessionEventService.start(this@MainActivity)
                    }
                }
            }
        }

        // Collected only while resumed, so a request made while nothing could
        // show the card is dropped without starting the cooldown.
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.RESUMED) {
                reviewPromptCoordinator.requests.collect { visit ->
                    inAppReviewManager.launchReview(
                        activity = this@MainActivity,
                        stillWanted = { reviewPromptCoordinator.isReviewMoment(visit) },
                        onLaunch = {
                            reviewLaunchedAtMs = SystemClock.elapsedRealtime()
                            reviewPromptCoordinator.markReviewLaunched()
                        },
                    )
                }
            }
        }

        lifecycleScope.launch {
            val outcome = versionGate.probe()
            if (outcome is ServerCompatibility.Incompatible) {
                sessionManager.markIncompatibleServer(
                    SessionManager.AuthState.IncompatibleServer(
                        reason = outcome.reason,
                        minClientVersion = outcome.minClientVersion,
                        playStoreUrl = outcome.playStoreUrl,
                        playStoreFallbackUrl = outcome.playStoreFallbackUrl,
                    )
                )
            }
        }
        // The saved theme must be known before the first frame: an async load
        // would flash the wrong theme on every recreation for users who chose
        // the opposite of the system mode (the window background resources can
        // only follow the system), and gating composition on the flow would
        // blank the app if the prefs file were unreadable. Blocking on the
        // tiny local DataStore file is the same tradeoff the web client makes
        // with its pre-paint theme script — but with a hard timeout well under
        // the ANR threshold, so a stalled read (slow storage, lock contention)
        // degrades to Auto for the first frame instead of freezing startup;
        // the composed collector below then corrects the theme when the value
        // eventually arrives.
        val initialThemeMode = runBlocking {
            runCatching {
                withTimeoutOrNull(THEME_LOAD_TIMEOUT_MS) { preferencesStore.themeMode.first() }
            }.getOrNull()
        } ?: ThemeMode.Auto
        setContent {
            val themeMode by preferencesStore.themeMode
                .collectAsStateWithLifecycle(initialValue = initialThemeMode)
            val darkTheme = when (themeMode) {
                ThemeMode.Auto -> isSystemInDarkTheme()
                ThemeMode.Light -> false
                ThemeMode.Dark -> true
            }
            // Android 12+ paints the cold-start splash from the system-qualified
            // theme before this process exists; registering the override with
            // the OS is the only way that starting window can follow it.
            LaunchedEffect(themeMode) { syncApplicationNightMode(themeMode) }
            // Re-assert bar icon contrast whenever the in-app override diverges
            // from the system theme (enableEdgeToEdge alone follows the system).
            DisposableEffect(darkTheme) {
                val transparent = android.graphics.Color.TRANSPARENT
                enableEdgeToEdge(
                    statusBarStyle = if (darkTheme) {
                        SystemBarStyle.dark(transparent)
                    } else {
                        SystemBarStyle.light(transparent, transparent)
                    },
                    navigationBarStyle = if (darkTheme) {
                        SystemBarStyle.dark(transparent)
                    } else {
                        SystemBarStyle.light(transparent, transparent)
                    },
                )
                onDispose {}
            }
            MobaideTheme(darkTheme = darkTheme) {
                AppNavigation(
                    sessionManager = sessionManager,
                    agentSwitchCoordinator = agentSwitchCoordinator,
                    deepLinkSessionId = deepLinkSessionId,
                    onDeepLinkConsumed = { deepLinkSessionId.value = null },
                    onImmediateUpdate = {
                        lifecycleScope.launch {
                            inAppUpdateManager.requestImmediateUpdate(this@MainActivity)
                        }
                    },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        consumeSessionExtra(intent)
    }

    override fun onResume() {
        super.onResume()
        // The review card pauses this activity and resumes it on close; a
        // flexible-update dialog right behind it would stack two Play pop-ups.
        val justReviewed = reviewLaunchedAtMs != 0L &&
            SystemClock.elapsedRealtime() - reviewLaunchedAtMs < UPDATE_CHECK_PAUSE_AFTER_REVIEW_MS
        if (!justReviewed) {
            lifecycleScope.launch {
                inAppUpdateManager.checkForUpdate(this@MainActivity)
            }
        }
    }

    private fun consumeSessionExtra(intent: Intent?) {
        val sessionId = intent?.getStringExtra(EXTRA_SESSION_ID)?.takeIf { it.isNotBlank() }
        if (sessionId != null) {
            deepLinkSessionId.value = sessionId
            // Clear so a config-change / re-create doesn't re-navigate.
            intent.removeExtra(EXTRA_SESSION_ID)
        }
    }

    /**
     * Persists the in-app theme as the app's per-package night mode. The
     * system applies it to this app's resource configuration (so `values-night`
     * and the splash resources resolve by the saved choice, not the device
     * setting); `Auto` clears the override. No-op below API 31, where the
     * theme is composed in-process and no system splash exists.
     */
    private fun syncApplicationNightMode(mode: ThemeMode) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
        val uiModeManager = getSystemService(UiModeManager::class.java) ?: return
        uiModeManager.setApplicationNightMode(
            when (mode) {
                ThemeMode.Auto -> UiModeManager.MODE_NIGHT_AUTO
                ThemeMode.Light -> UiModeManager.MODE_NIGHT_NO
                ThemeMode.Dark -> UiModeManager.MODE_NIGHT_YES
            },
        )
    }

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            requestNotificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    companion object {
        const val EXTRA_SESSION_ID = "com.mobaide.app.EXTRA_SESSION_ID"

        /** Normal reads finish in single-digit ms; far below the ~5s ANR bar. */
        private const val THEME_LOAD_TIMEOUT_MS = 500L

        private const val UPDATE_CHECK_PAUSE_AFTER_REVIEW_MS = 60_000L
    }
}
