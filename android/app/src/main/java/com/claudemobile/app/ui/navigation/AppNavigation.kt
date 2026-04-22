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
import com.claudemobile.app.ui.agents.AgentListScreen
import com.claudemobile.app.ui.autoscan.AutoScanDetailScreen
import com.claudemobile.app.ui.connections.ConnectionsScreen
import com.claudemobile.app.ui.login.LoginScreen
import com.claudemobile.app.ui.pairing.ManualPairingScreen
import com.claudemobile.app.ui.pairing.PairingViewModel
import com.claudemobile.app.ui.pairing.QrScannerScreen
import com.claudemobile.app.ui.projects.ProjectsScreen
import com.claudemobile.app.ui.register.RegisterScreen
import com.claudemobile.app.ui.register.VerifyEmailScreen
import com.claudemobile.app.ui.sessions.CreateSessionScreen
import com.claudemobile.app.ui.sessions.SessionListScreen
import com.claudemobile.app.ui.settings.SettingsScreen
import com.claudemobile.app.ui.terminal.TerminalScreen

@Composable
fun AppNavigation(sessionManager: SessionManager) {
    val navController = rememberNavController()
    val authState by sessionManager.authState.collectAsStateWithLifecycle()

    // Both authenticated states land on AGENT_LIST; only Unauthenticated goes to LOGIN.
    val startDestination = when (sessionManager.authState.value) {
        SessionManager.AuthState.FullyConnected -> Routes.AGENT_LIST
        SessionManager.AuthState.UserOnly -> Routes.AGENT_LIST
        SessionManager.AuthState.Unauthenticated -> Routes.LOGIN
    }

    // Track previous state to skip navigation on cold start; only react to actual transitions.
    var previousState by remember { mutableStateOf(sessionManager.authState.value) }
    LaunchedEffect(authState) {
        val current = authState
        when {
            current is SessionManager.AuthState.Unauthenticated
                && previousState !is SessionManager.AuthState.Unauthenticated -> {
                navController.navigate(Routes.LOGIN) {
                    popUpTo(0) { inclusive = true }
                    launchSingleTop = true
                }
            }
            current is SessionManager.AuthState.UserOnly
                && previousState is SessionManager.AuthState.FullyConnected -> {
                navController.navigate(Routes.AGENT_LIST) {
                    popUpTo(0) { inclusive = true }
                    launchSingleTop = true
                }
            }
        }
        previousState = current
    }

    NavHost(navController = navController, startDestination = startDestination) {
        composable(Routes.LOGIN) {
            LoginScreen(
                viewModel = hiltViewModel(),
                onLoginSuccess = {
                    navController.navigate(Routes.AGENT_LIST) {
                        popUpTo(Routes.LOGIN) { inclusive = true }
                    }
                },
                onVerificationRequired = { email ->
                    navController.navigate(Routes.verifyEmail(email))
                },
                onRegisterClick = {
                    navController.navigate(Routes.REGISTER)
                },
            )
        }

        composable(Routes.REGISTER) {
            RegisterScreen(
                viewModel = hiltViewModel(),
                onRegisterSuccess = {
                    navController.navigate(Routes.AGENT_LIST) {
                        popUpTo(Routes.REGISTER) { inclusive = true }
                    }
                },
                onVerificationRequired = { email ->
                    navController.navigate(Routes.verifyEmail(email))
                },
                onLoginClick = {
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(Routes.REGISTER) { inclusive = true }
                    }
                },
            )
        }

        composable(
            route = Routes.VERIFY_EMAIL,
            arguments = listOf(navArgument("email") { type = NavType.StringType }),
        ) { entry ->
            VerifyEmailScreen(
                email = entry.arguments?.getString("email").orEmpty(),
                viewModel = hiltViewModel(),
                onVerificationSuccess = {
                    navController.navigate(Routes.AGENT_LIST) {
                        popUpTo(0) { inclusive = true }
                    }
                },
                onChangeEmail = { navController.popBackStack() },
            )
        }

        composable(Routes.QR_SCANNER) {
            val pairingViewModel: PairingViewModel = hiltViewModel()
            QrScannerScreen(
                viewModel = pairingViewModel,
                onPairingComplete = {
                    navController.navigate(Routes.AGENT_LIST) {
                        popUpTo(Routes.LOGIN) { inclusive = true }
                    }
                },
                onManualEntry = {
                    navController.navigate(Routes.MANUAL_PAIRING)
                },
                onBack = { navController.popBackStack() },
            )
        }

        composable(Routes.MANUAL_PAIRING) {
            val pairingViewModel: PairingViewModel = hiltViewModel()
            ManualPairingScreen(
                viewModel = pairingViewModel,
                onPairingComplete = {
                    navController.navigate(Routes.AGENT_LIST) {
                        popUpTo(Routes.LOGIN) { inclusive = true }
                    }
                },
                onBack = { navController.popBackStack() },
            )
        }

        composable(Routes.AGENT_LIST) {
            AgentListScreen(
                viewModel = hiltViewModel(),
                onAgentConnected = {
                    navController.navigate(Routes.SESSION_LIST)
                },
                onSettingsClick = {
                    navController.navigate(Routes.SETTINGS)
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
                onProjectsClick = {
                    navController.navigate(Routes.PROJECTS)
                },
                onAutoScanClick = { projectName ->
                    navController.navigate(Routes.autoScanDetail(projectName))
                },
                onBack = { navController.popBackStack() },
            )
        }

        composable(Routes.PROJECTS) {
            ProjectsScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
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
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(0) { inclusive = true }
                    }
                },
                onManageConnections = {
                    navController.navigate(Routes.AGENT_LIST) {
                        popUpTo(Routes.AGENT_LIST) { inclusive = true }
                    }
                },
            )
        }

        composable(Routes.CONNECTIONS) {
            ConnectionsScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
                onAddConnection = {
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(Routes.SESSION_LIST)
                    }
                },
            )
        }
    }
}
