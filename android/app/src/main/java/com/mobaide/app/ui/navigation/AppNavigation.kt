package com.mobaide.app.ui.navigation

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
import com.mobaide.app.data.auth.SessionManager
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import com.mobaide.app.ui.agents.AgentListScreen
import com.mobaide.app.ui.autoscan.AutoScanDetailScreen
import com.mobaide.app.ui.connections.ConnectionsScreen
import com.mobaide.app.ui.devices.DevicesScreen
import com.mobaide.app.ui.login.ForgotPasswordScreen
import com.mobaide.app.ui.login.LoginScreen
import com.mobaide.app.ui.login.ResetPasswordScreen
import com.mobaide.app.ui.projects.ProjectsScreen
import com.mobaide.app.ui.projects.git.GitScreen
import com.mobaide.app.ui.register.RegisterScreen
import com.mobaide.app.ui.register.VerifyEmailScreen
import com.mobaide.app.ui.sessions.CreateSessionScreen
import com.mobaide.app.ui.sessions.SessionListScreen
import com.mobaide.app.ui.settings.SettingsScreen
import com.mobaide.app.ui.support.SupportScreen
import com.mobaide.app.ui.templates.TemplatesScreen
import com.mobaide.app.ui.terminal.TerminalScreen
import com.mobaide.app.ui.terminal.git.GitChangesScreen
import com.mobaide.app.ui.terminal.git.GitDiffScreen
import com.mobaide.app.ui.update.IncompatibleServerScreen

@Composable
fun AppNavigation(
    sessionManager: SessionManager,
    deepLinkSessionId: StateFlow<String?> = MutableStateFlow<String?>(null),
    onDeepLinkConsumed: () -> Unit = {},
    onImmediateUpdate: () -> Unit = {},
) {
    val navController = rememberNavController()
    val authState by sessionManager.authState.collectAsStateWithLifecycle()
    val deepLink by deepLinkSessionId.collectAsStateWithLifecycle()

    // IncompatibleServer collapses the entire navigation graph onto the
    // upgrade screen. The user must update the app (via the Play Store deep
    // link) before any other route renders.
    val incompatible = authState as? SessionManager.AuthState.IncompatibleServer
    if (incompatible != null) {
        IncompatibleServerScreen(state = incompatible, onImmediateUpdate = onImmediateUpdate)
        return
    }

    val startDestination = when (sessionManager.authState.value) {
        SessionManager.AuthState.FullyConnected -> Routes.AGENT_LIST
        SessionManager.AuthState.UserOnly -> Routes.AGENT_LIST
        SessionManager.AuthState.Unauthenticated -> Routes.LOGIN
        is SessionManager.AuthState.IncompatibleServer -> Routes.INCOMPATIBLE_SERVER
    }

    // Track previous state to skip navigation on cold start; only react to actual transitions.
    var previousState by remember { mutableStateOf(sessionManager.authState.value) }
    LaunchedEffect(authState) {
        val current = authState
        when {
            current is SessionManager.AuthState.IncompatibleServer -> Unit
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

    // A tapped attention notification deep-links to its session's terminal.
    // Wait until FullyConnected so the SESSION_DETAIL route is reachable, then
    // consume so a recomposition / config-change doesn't re-navigate.
    LaunchedEffect(deepLink, authState) {
        val sessionId = deepLink ?: return@LaunchedEffect
        if (authState == SessionManager.AuthState.FullyConnected) {
            navController.navigate(Routes.sessionDetail(sessionId)) {
                launchSingleTop = true
            }
            onDeepLinkConsumed()
        }
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
                onForgotPasswordClick = {
                    navController.navigate(Routes.FORGOT_PASSWORD)
                },
                onHelpSupportClick = {
                    navController.navigate(Routes.SUPPORT)
                },
            )
        }

        composable(Routes.FORGOT_PASSWORD) {
            ForgotPasswordScreen(
                viewModel = hiltViewModel(),
                onCodeSent = { email ->
                    navController.navigate(Routes.resetPassword(email))
                },
                onBack = { navController.popBackStack() },
            )
        }

        composable(
            route = Routes.RESET_PASSWORD,
            arguments = listOf(navArgument("email") { type = NavType.StringType }),
        ) { entry ->
            ResetPasswordScreen(
                email = entry.arguments?.getString("email").orEmpty(),
                viewModel = hiltViewModel(),
                onResetSuccess = {
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(Routes.LOGIN) { inclusive = true }
                    }
                },
                onBack = { navController.popBackStack() },
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

        composable(Routes.AGENT_LIST) {
            AgentListScreen(
                viewModel = hiltViewModel(),
                onAgentConnected = {
                    // Without this, re-entering from the agent list stacks a
                    // second SessionListViewModel whose auto-resume arms afresh
                    // and pushes another terminal on top.
                    navController.navigate(Routes.SESSION_LIST) {
                        launchSingleTop = true
                    }
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
                    navController.navigate(Routes.sessionDetail(sessionId)) {
                        launchSingleTop = true
                    }
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
                onOpenGit = { name ->
                    navController.navigate(Routes.gitManagement(name))
                },
            )
        }

        composable(
            route = Routes.GIT_MANAGEMENT,
            arguments = listOf(navArgument("projectName") { type = NavType.StringType }),
        ) { entry ->
            GitScreen(
                projectName = entry.arguments?.getString("projectName").orEmpty(),
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
            )
        }

        composable(Routes.CREATE_SESSION) {
            CreateSessionScreen(
                viewModel = hiltViewModel(),
                projectsViewModel = hiltViewModel(),
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
        ) { entry ->
            TerminalScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
                // The session list is not always behind us — a notification tap
                // or a restored back stack can land here directly — so fall back
                // to navigating there when there is nothing to pop back to.
                onSessionEnded = {
                    if (!navController.popBackStack(Routes.SESSION_LIST, false)) {
                        navController.navigate(Routes.SESSION_LIST) {
                            popUpTo(Routes.AGENT_LIST)
                            launchSingleTop = true
                        }
                    }
                },
                onOpenGitChanges = {
                    entry.arguments?.getString("sessionId")?.let { sessionId ->
                        navController.navigate(Routes.sessionGitChanges(sessionId))
                    }
                },
            )
        }

        composable(
            route = Routes.SESSION_GIT_CHANGES,
            arguments = listOf(navArgument("sessionId") { type = NavType.StringType }),
        ) {
            GitChangesScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
                onOpenDiff = { sessionId, entry, scope ->
                    navController.navigate(
                        Routes.sessionGitDiff(sessionId, entry.path, scope, entry.orig_path),
                    )
                },
            )
        }

        composable(
            route = Routes.SESSION_GIT_DIFF,
            arguments = listOf(
                navArgument("sessionId") { type = NavType.StringType },
                navArgument("path") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("scope") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("origPath") { type = NavType.StringType; nullable = true; defaultValue = null },
            ),
        ) {
            GitDiffScreen(
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
                onManageTemplates = {
                    navController.navigate(Routes.TEMPLATES)
                },
                onManageDevices = {
                    navController.navigate(Routes.DEVICES)
                },
                onOpenSupport = {
                    navController.navigate(Routes.SUPPORT)
                },
            )
        }

        composable(Routes.SUPPORT) {
            SupportScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
            )
        }

        composable(Routes.TEMPLATES) {
            TemplatesScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
            )
        }

        composable(Routes.DEVICES) {
            DevicesScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
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
