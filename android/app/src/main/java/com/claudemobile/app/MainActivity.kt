package com.claudemobile.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.lifecycleScope
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.data.events.UserEventBus
import com.claudemobile.app.data.repository.ServerCompatibility
import com.claudemobile.app.data.repository.VersionGateRepository
import com.claudemobile.app.ui.navigation.AppNavigation
import com.claudemobile.app.ui.theme.ClaudeMobileTheme
import com.claudemobile.app.ui.update.InAppUpdateManager
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.launch
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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        agentEventBus.start()
        userEventBus.start()
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
        setContent {
            ClaudeMobileTheme {
                AppNavigation(sessionManager = sessionManager)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        lifecycleScope.launch {
            inAppUpdateManager.checkForUpdate(this@MainActivity)
        }
    }
}
