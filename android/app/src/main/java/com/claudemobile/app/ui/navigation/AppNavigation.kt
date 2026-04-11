package com.claudemobile.app.ui.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.ui.autoscan.AutoScanDetailScreen
import com.claudemobile.app.ui.login.LoginScreen
import com.claudemobile.app.ui.sessions.CreateSessionScreen
import com.claudemobile.app.ui.sessions.SessionListScreen
import com.claudemobile.app.ui.settings.SettingsScreen
import com.claudemobile.app.ui.terminal.TerminalScreen

@Composable
fun AppNavigation(sessionManager: SessionManager) {
    val navController = rememberNavController()
    val authState by sessionManager.authState.collectAsStateWithLifecycle()

    val startDestination = if (sessionManager.isLoggedIn()) {
        Routes.SESSION_LIST
    } else {
        Routes.LOGIN
    }

    // Only react to the LoggedIn → LoggedOut transition, not to cold
    // starts where the initial state is already LoggedOut (which is
    // handled by `startDestination` above). Tracking the previous value
    // in a remember avoids re-navigating on process recreation and on
    // every LaunchedEffect re-key.
    var wasLoggedIn by remember { mutableStateOf(sessionManager.isLoggedIn()) }
    LaunchedEffect(authState) {
        val nowLoggedIn = authState is SessionManager.AuthState.LoggedIn
        if (wasLoggedIn && !nowLoggedIn) {
            navController.navigate(Routes.LOGIN) {
                // Pop everything, including the start destination, so
                // the user can't hit "back" from Login to a stale
                // authenticated screen.
                popUpTo(0) { inclusive = true }
                launchSingleTop = true
            }
        }
        wasLoggedIn = nowLoggedIn
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
                onAutoScanClick = { projectName ->
                    navController.navigate(Routes.autoScanDetail(projectName))
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
                onAutoScanStarted = { projectName ->
                    navController.navigate(Routes.autoScanDetail(projectName)) {
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

        composable(
            route = Routes.AUTO_SCAN_DETAIL,
            arguments = listOf(navArgument("projectName") { type = NavType.StringType }),
        ) { entry ->
            AutoScanDetailScreen(
                projectName = entry.arguments?.getString("projectName").orEmpty(),
                onBack = { navController.popBackStack() },
                viewModel = hiltViewModel(),
            )
        }

        composable(Routes.SETTINGS) {
            SettingsScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
                onLogout = {
                    // SessionManager.forceLogout (via
                    // SettingsViewModel.logout → AuthRepository.logout)
                    // flips authState to LoggedOut, and the LaunchedEffect
                    // above handles the actual navigation. The explicit
                    // navigate call here is left as a belt-and-suspenders
                    // fallback so the UX doesn't depend on the auth
                    // observer being wired.
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(0) { inclusive = true }
                    }
                },
            )
        }
    }
}
