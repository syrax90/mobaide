package com.mobaide.app.ui.navigation

import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavBackStackEntry
import androidx.navigation.NavController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.repository.AgentSwitchCoordinator
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
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.ui.terminal.git.GitChangesScreen
import com.mobaide.app.ui.terminal.git.GitConflictScreen
import com.mobaide.app.ui.terminal.git.GitConflictViewModel
import com.mobaide.app.ui.terminal.git.GitDiffScreen
import com.mobaide.app.ui.update.IncompatibleServerScreen

@Composable
fun AppNavigation(
    sessionManager: SessionManager,
    agentSwitchCoordinator: AgentSwitchCoordinator,
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

    // An agent switch (agent list or the terminal's sessions drawer): the
    // disconnect above already reset the stack onto the agent list, so the new
    // agent's sessions list goes on top of it once the connection is back.
    val pendingSessionsList by agentSwitchCoordinator.pendingSessionsList.collectAsStateWithLifecycle()
    LaunchedEffect(pendingSessionsList, authState) {
        if (!pendingSessionsList || authState != SessionManager.AuthState.FullyConnected) return@LaunchedEffect
        navController.navigate(Routes.SESSION_LIST) {
            popUpTo(Routes.AGENT_LIST)
            launchSingleTop = true
        }
        agentSwitchCoordinator.consumePendingSessionsList()
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
                    navController.navigateFromCreate(Routes.sessionDetail(sessionId))
                },
                onAutoScanStarted = { projectName ->
                    navController.navigateFromCreate(Routes.autoScanDetail(projectName))
                },
            )
        }

        composable(
            route = Routes.SESSION_DETAIL,
            arguments = listOf(
                navArgument("sessionId") { type = NavType.StringType },
                navArgument("stopped") {
                    type = NavType.StringType
                    nullable = true
                    defaultValue = null
                },
            ),
            // A stop hand-off swaps terminals under an open drawer that both
            // screens draw identically; the default cross-fade would dim it.
            // The terminal's own fade-out/in carries the switch instead.
            enterTransition = {
                if (targetState.isStopHandOff()) EnterTransition.None else null
            },
            exitTransition = {
                if (targetState.isStopHandOff()) ExitTransition.None else null
            },
        ) { entry ->
            TerminalScreen(
                viewModel = hiltViewModel(),
                voiceViewModel = hiltViewModel(),
                stoppedSessionId = entry.arguments?.getString("stopped"),
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
                onOpenGitCommit = {
                    entry.arguments?.getString("sessionId")?.let { sessionId ->
                        navController.navigate(Routes.sessionGitChanges(sessionId, commit = true))
                    }
                },
                onOpenGitSync = {
                    entry.arguments?.getString("sessionId")?.let { sessionId ->
                        navController.navigate(Routes.sessionGitChanges(sessionId, sync = true))
                    }
                },
                onOpenGitBranches = { name ->
                    navController.navigate(Routes.gitManagement(name))
                },
                onOpenGitRebase = {
                    entry.arguments?.getString("sessionId")?.let { sessionId ->
                        navController.navigate(Routes.sessionGitChanges(sessionId, rebase = true))
                    }
                },
                // Replace, never stack: Back from the new terminal still returns
                // to the sessions list. `launchSingleTop` alone would keep this
                // entry (and its ViewModel, bound to the old session id).
                onSwitchSession = { sessionId ->
                    navController.navigate(Routes.sessionDetail(sessionId)) {
                        popUpTo(Routes.SESSION_DETAIL) { inclusive = true }
                        launchSingleTop = true
                    }
                },
                onSwitchAfterStop = { sessionId, stoppedSessionId ->
                    navController.navigate(Routes.sessionDetail(sessionId, stoppedSessionId)) {
                        popUpTo(Routes.SESSION_DETAIL) { inclusive = true }
                        launchSingleTop = true
                    }
                },
                onCreateSession = {
                    navController.navigate(Routes.CREATE_SESSION)
                },
            )
        }

        composable(
            route = Routes.SESSION_GIT_CHANGES,
            arguments = listOf(
                navArgument("sessionId") { type = NavType.StringType },
                navArgument("commit") { type = NavType.BoolType; defaultValue = false },
                navArgument("sync") { type = NavType.BoolType; defaultValue = false },
                navArgument("rebase") { type = NavType.BoolType; defaultValue = false },
            ),
        ) { entry ->
            val conflictMessage by entry.savedStateHandle
                .getStateFlow<String?>(Routes.RESULT_GIT_CONFLICT_MESSAGE, null)
                .collectAsStateWithLifecycle()
            GitChangesScreen(
                viewModel = hiltViewModel(),
                onBack = { navController.popBackStack() },
                onOpenDiff = { sessionId, row, scope ->
                    navController.navigate(
                        Routes.sessionGitDiff(sessionId, row.path, scope, row.orig_path),
                    )
                },
                onOpenConflict = { sessionId, path ->
                    navController.navigate(Routes.sessionGitConflict(sessionId, path))
                },
                resultMessage = conflictMessage,
                onResultMessageShown = {
                    entry.savedStateHandle[Routes.RESULT_GIT_CONFLICT_MESSAGE] = null
                },
            )
        }

        composable(
            route = Routes.SESSION_GIT_CONFLICT,
            arguments = listOf(
                navArgument("sessionId") { type = NavType.StringType },
                navArgument("path") { type = NavType.StringType; defaultValue = "" },
            ),
        ) {
            val viewModel: GitConflictViewModel = hiltViewModel()
            GitConflictScreen(
                viewModel = viewModel,
                onBack = { navController.popBackStack() },
                onClosed = { message ->
                    navController.previousBackStackEntry
                        ?.savedStateHandle
                        ?.set(Routes.RESULT_GIT_CONFLICT_MESSAGE, message)
                    navController.popBackStack()
                },
                onViewDiff = {
                    navController.navigate(
                        Routes.sessionGitDiff(
                            viewModel.sessionId,
                            viewModel.path,
                            GitDiffResponse.SCOPE_UNSTAGED,
                            null,
                        ),
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
                voiceViewModel = hiltViewModel(),
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

/**
 * Leaves a finished create flow on `route` with the sessions list beneath it.
 * Normally that is a `popUpTo(SESSION_LIST)`; but the terminal's drawer can
 * open the create screen over a terminal that has no list beneath it (a
 * notification tap, a restored stack), where that pop would do nothing and
 * leave the used form and the old terminal behind the new screen. The list
 * is re-created first then, so Back behaves the same either way.
 */
private fun NavController.navigateFromCreate(route: String) {
    if (runCatching { getBackStackEntry(Routes.SESSION_LIST) }.isFailure) {
        navigate(Routes.SESSION_LIST) {
            popUpTo(Routes.AGENT_LIST)
            launchSingleTop = true
        }
    }
    navigate(route) {
        popUpTo(Routes.SESSION_LIST)
    }
}

private fun NavBackStackEntry.isStopHandOff(): Boolean =
    destination.route == Routes.SESSION_DETAIL && arguments?.getString("stopped") != null
