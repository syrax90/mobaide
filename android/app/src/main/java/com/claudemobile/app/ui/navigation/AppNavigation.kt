package com.claudemobile.app.ui.navigation

import androidx.compose.runtime.Composable
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.claudemobile.app.data.auth.TokenStore
import com.claudemobile.app.ui.login.LoginScreen
import com.claudemobile.app.ui.sessions.CreateSessionScreen
import com.claudemobile.app.ui.sessions.SessionListScreen
import com.claudemobile.app.ui.settings.SettingsScreen
import com.claudemobile.app.ui.terminal.TerminalScreen

@Composable
fun AppNavigation(tokenStore: TokenStore? = null) {
    val navController = rememberNavController()
    val startDestination = if (tokenStore?.isLoggedIn() == true) {
        Routes.SESSION_LIST
    } else {
        Routes.LOGIN
    }

    NavHost(navController = navController, startDestination = startDestination) {
        composable(Routes.LOGIN) {
            LoginScreen(
                viewModel = hiltViewModel(),
                onLoginSuccess = {
                    navController.navigate(Routes.SESSION_LIST) {
                        popUpTo(Routes.LOGIN) { inclusive = true }
                    }
                },
            )
        }

        composable(Routes.SESSION_LIST) {
            SessionListScreen(
                viewModel = hiltViewModel(),
                onSessionClick = { sessionId ->
                    navController.navigate(Routes.sessionDetail(sessionId))
                },
                onCreateClick = {
                    navController.navigate(Routes.CREATE_SESSION)
                },
                onSettingsClick = {
                    navController.navigate(Routes.SETTINGS)
                },
            )
        }

        composable(Routes.CREATE_SESSION) {
            CreateSessionScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
                onSessionCreated = { sessionId ->
                    navController.navigate(Routes.sessionDetail(sessionId)) {
                        popUpTo(Routes.SESSION_LIST)
                    }
                },
            )
        }

        composable(
            route = Routes.SESSION_DETAIL,
            arguments = listOf(navArgument("sessionId") { type = NavType.StringType }),
        ) {
            TerminalScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
            )
        }

        composable(Routes.SETTINGS) {
            SettingsScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
                onLogout = {
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(0) { inclusive = true }
                    }
                },
            )
        }
    }
}
