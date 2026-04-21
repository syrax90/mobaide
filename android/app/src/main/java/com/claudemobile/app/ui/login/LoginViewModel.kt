package com.claudemobile.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.auth.GoogleAuthResult
import com.claudemobile.app.data.auth.SessionManager
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.repository.ADMIN_LOGIN_USER_MESSAGE
import com.claudemobile.app.data.repository.AdminLoginNotSupportedException
import com.claudemobile.app.data.repository.AuthRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class LoginUiState(
    val email: String = "",
    val password: String = "",
    val isLoading: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val sessionManager: SessionManager,
) : ViewModel() {

    private val _uiState = MutableStateFlow(LoginUiState())
    val uiState: StateFlow<LoginUiState> = _uiState.asStateFlow()

    private val _loginSuccess = MutableStateFlow(false)
    val loginSuccess: StateFlow<Boolean> = _loginSuccess.asStateFlow()

    // One-shot error channel so duplicate error messages still trigger the snackbar.
    private val _errorEvents = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val errorEvents: SharedFlow<String> = _errorEvents.asSharedFlow()

    init {
        // Surface "session expired" when the session was invalidated by
        // the server rejecting the dashboard JWT (central-server 401 or
        // user-scoped WS 4001). Manual logouts are silent — the user
        // pressed logout themselves and doesn't need a toast saying so.
        viewModelScope.launch {
            sessionManager.sessionEvents.collect { reason ->
                if (reason is SessionManager.InvalidationReason.TokenRejected) {
                    _errorEvents.tryEmit(
                        "Your session has expired. Please sign in again.",
                    )
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
                authRepository.loginWithPassword(state.email.trim(), state.password)
                _loginSuccess.value = true
            } catch (e: AdminLoginNotSupportedException) {
                emitError(ADMIN_LOGIN_USER_MESSAGE)
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
                authRepository.loginWithGoogle(idToken)
                _loginSuccess.value = true
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
