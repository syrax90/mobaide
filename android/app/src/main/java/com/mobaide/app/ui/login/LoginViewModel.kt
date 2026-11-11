package com.mobaide.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.BuildConfig
import com.mobaide.app.data.auth.DevLoginHandoff
import com.mobaide.app.data.auth.GoogleAuthResult
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.repository.ADMIN_LOGIN_USER_MESSAGE
import com.mobaide.app.data.repository.AdminLoginNotSupportedException
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.AuthResult
import com.mobaide.app.data.repository.LoginRateLimitedException
import com.mobaide.app.data.repository.loginRateLimitedMessage
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class LoginUiState(
    val email: String = "",
    val password: String = "",
    val isLoading: Boolean = false,
    val error: String? = null,
)

/** One-shot navigation events from the login screen. */
sealed interface LoginNavEvent {
    data class Success(val email: String) : LoginNavEvent
    data class VerificationRequired(val email: String) : LoginNavEvent
}

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val sessionManager: SessionManager,
) : ViewModel() {

    private val _uiState = MutableStateFlow(LoginUiState())
    val uiState: StateFlow<LoginUiState> = _uiState.asStateFlow()

    private val _navEvents = MutableSharedFlow<LoginNavEvent>(extraBufferCapacity = 1)
    val navEvents: SharedFlow<LoginNavEvent> = _navEvents.asSharedFlow()

    // One-shot error channel so duplicate error messages still trigger the snackbar.
    private val _errorEvents = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val errorEvents: SharedFlow<String> = _errorEvents.asSharedFlow()

    init {
        // Surface a message tailored to the reason the session was
        // invalidated. Manual logouts stay silent — the user pressed
        // logout themselves and doesn't need a toast saying so.
        viewModelScope.launch {
            sessionManager.sessionEvents.collect { reason ->
                when (reason) {
                    is SessionManager.InvalidationReason.TokenRejected ->
                        _errorEvents.tryEmit(
                            "Your session has expired. Please sign in again.",
                        )
                    is SessionManager.InvalidationReason.AccountRemoved ->
                        _errorEvents.tryEmit(
                            "Your account was removed by an administrator. " +
                                "Sign up to start again or contact support.",
                        )
                    is SessionManager.InvalidationReason.Manual -> Unit
                }
            }
        }
        if (BuildConfig.DEBUG) {
            viewModelScope.launch {
                DevLoginHandoff.pending.filterNotNull().collect { credentials ->
                    if (DevLoginHandoff.take(credentials)) {
                        _uiState.update {
                            it.copy(email = credentials.email, password = credentials.password, error = null)
                        }
                        loginWithPassword()
                    }
                }
            }
        }
    }

    fun updateEmail(email: String) {
        _uiState.update { it.copy(email = email, error = null) }
    }

    fun updatePassword(password: String) {
        _uiState.update { it.copy(password = password, error = null) }
    }

    fun loginWithPassword() {
        val state = _uiState.value
        if (state.email.isBlank() || state.password.isBlank()) {
            emitError("Email and password are required")
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                val result = authRepository.loginWithPassword(state.email.trim(), state.password)
                _uiState.update { it.copy(isLoading = false) }
                when (result) {
                    is AuthResult.Success ->
                        _navEvents.tryEmit(LoginNavEvent.Success(result.email))
                    is AuthResult.VerificationRequired ->
                        _navEvents.tryEmit(LoginNavEvent.VerificationRequired(result.email))
                }
            } catch (e: AdminLoginNotSupportedException) {
                emitError(ADMIN_LOGIN_USER_MESSAGE)
            } catch (e: LoginRateLimitedException) {
                emitError(loginRateLimitedMessage(e.retryAfterSeconds))
            } catch (e: ApiException) {
                val message = when (e.code) {
                    401 -> "Invalid email or password"
                    else -> "Login failed: ${e.message}"
                }
                emitError(message)
            } catch (e: Exception) {
                emitError("Login failed: ${e.message}")
            }
        }
    }

    fun onGoogleAuthStart() {
        _uiState.update { it.copy(isLoading = true, error = null) }
    }

    fun onGoogleAuthResult(result: GoogleAuthResult) {
        when (result) {
            is GoogleAuthResult.Success -> onGoogleIdToken(result.idToken)
            GoogleAuthResult.Cancelled -> {
                _uiState.update { it.copy(isLoading = false) }
            }
            GoogleAuthResult.NotConfigured -> emitError(
                "Google Sign-In is not configured for this build. Contact the developer.",
            )
            is GoogleAuthResult.NoAccounts -> emitError(result.message)
            GoogleAuthResult.PlayServicesUnavailable -> emitError(
                "Google Play services are unavailable. Update Google Play services and try again.",
            )
            is GoogleAuthResult.Failed -> emitError("Google Sign-In failed: ${result.message}")
        }
    }

    private fun onGoogleIdToken(idToken: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                val result = authRepository.loginWithGoogle(idToken)
                _uiState.update { it.copy(isLoading = false) }
                when (result) {
                    is AuthResult.Success ->
                        _navEvents.tryEmit(LoginNavEvent.Success(result.email))
                    is AuthResult.VerificationRequired ->
                        _navEvents.tryEmit(LoginNavEvent.VerificationRequired(result.email))
                }
            } catch (e: AdminLoginNotSupportedException) {
                emitError(ADMIN_LOGIN_USER_MESSAGE)
            } catch (e: ApiException) {
                emitError("Google Sign-In failed: ${e.message}")
            } catch (e: Exception) {
                emitError("Google Sign-In failed: ${e.message}")
            }
        }
    }

    private fun emitError(message: String) {
        _uiState.update { it.copy(isLoading = false, error = message) }
        _errorEvents.tryEmit(message)
    }
}
