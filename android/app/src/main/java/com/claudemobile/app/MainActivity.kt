package com.claudemobile.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.events.AgentEventBus
import com.claudemobile.app.ui.navigation.AppNavigation
import com.claudemobile.app.ui.theme.ClaudeMobileTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var sessionManager: SessionManager

    @Inject
    lateinit var agentEventBus: AgentEventBus

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Idempotent — safe to call every onCreate, no-ops if the user
        // is not logged in. The AgentEventBus also auto-reacts to
        // session state transitions (LoggedOut → stop, subsequent
        // LoggedIn → start), so a mid-session logout doesn't leak the
        // connection.
        agentEventBus.start()
        setContent {
            ClaudeMobileTheme {
                AppNavigation(sessionManager = sessionManager)
            }
        }
    }
}
