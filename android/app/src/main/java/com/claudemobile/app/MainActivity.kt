package com.claudemobile.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.ui.navigation.AppNavigation
import com.claudemobile.app.ui.theme.ClaudeMobileTheme
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject
    lateinit var tokenStore: TokenStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ClaudeMobileTheme {
                AppNavigation(tokenStore = tokenStore)
            }
        }
    }
}
