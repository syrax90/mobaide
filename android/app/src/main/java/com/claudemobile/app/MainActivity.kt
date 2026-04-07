package com.claudemobile.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.ui.navigation.AppNavigation
import com.claudemobile.app.ui.theme.ClaudeMobileTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var tokenStore: TokenStore

    @Inject
    lateinit var serverEventBus: ServerEventBus

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        // Idempotent — safe even before login; will quietly no-op until a
        // server URL + token are available, at which point any screen that
        // calls start() again (e.g. SessionListViewModel.init) will succeed.
        serverEventBus.start()
        setContent {
            ClaudeMobileTheme {
                AppNavigation(tokenStore = tokenStore)
            }
        }
    }
}
