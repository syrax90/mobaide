package com.mobaide.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.events.UserEventBus
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.ThemeMode
import com.mobaide.app.data.repository.ServerCompatibility
import com.mobaide.app.data.repository.VersionGateRepository
import com.mobaide.app.notifications.SessionEventService
import com.mobaide.app.ui.navigation.AppNavigation
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.update.InAppUpdateManager
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var sessionManager: SessionManager

    @Inject
    lateinit var agentEventBus: AgentEventBus

    @Inject
    lateinit var userEventBus: UserEventBus

    @Inject
    lateinit var versionGate: VersionGateRepository

    @Inject
    lateinit var inAppUpdateManager: InAppUpdateManager

    @Inject
    lateinit var preferencesStore: PreferencesStore

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
        // blank the app if the prefs file were unreadable. A bounded blocking
        // read of the tiny local DataStore file is the same tradeoff the web
        // client makes with its pre-paint theme script; failures degrade to
        // Auto both here and inside the flow itself.
        val initialThemeMode = runBlocking {
            runCatching { preferencesStore.themeMode.first() }.getOrDefault(ThemeMode.Auto)
        }
        setContent {
            val themeMode by preferencesStore.themeMode
                .collectAsStateWithLifecycle(initialValue = initialThemeMode)
            val darkTheme = when (themeMode) {
                ThemeMode.Auto -> isSystemInDarkTheme()
                ThemeMode.Light -> false
                ThemeMode.Dark -> true
            }
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
        lifecycleScope.launch {
            inAppUpdateManager.checkForUpdate(this@MainActivity)
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
    }
}
