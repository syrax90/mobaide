package com.claudemobile.app.ui.register

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.auth.GoogleAuthResult
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.repository.ADMIN_LOGIN_USER_MESSAGE
import com.claudemobile.app.data.repository.AdminLoginNotSupportedException
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.data.repository.AuthResult
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

data class RegisterUiState(
    val email: String = "",
    val password: String = "",
    val confirmPassword: String = "",
    val isLoading: Boolean = false,
    val error: String? = null,
)

/** One-shot navigation events from the register screen. */
sealed interface RegisterNavEvent {
    data class Success(val email: String) : RegisterNavEvent
    data class VerificationRequired(val email: String) : RegisterNavEvent
}

@HiltViewModel
class RegisterViewModel @Inject constructor(
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(RegisterUiState())
    val uiState: StateFlow<RegisterUiState> = _uiState.asStateFlow()

    private val _navEvents = MutableSharedFlow<RegisterNavEvent>(extraBufferCapacity = 1)
    val navEvents: SharedFlow<RegisterNavEvent> = _navEvents.asSharedFlow()

    private val _errorEvents = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val errorEvents: SharedFlow<String> = _errorEvents.asSharedFlow()

    fun updateEmail(email: String) {
        _uiState.update { it.copy(email = email, error = null) }
    }

    fun updatePassword(password: String) {
        _uiState.update { it.copy(password = password, error = null) }
    }

    fun updateConfirmPassword(confirmPassword: String) {
        _uiState.update { it.copy(confirmPassword = confirmPassword, error = null) }
    }

    fun register() {
        val state = _uiState.value
        if (state.email.isBlank() || state.password.isBlank()) {
            emitError("Email and password are required")
            return
        }
        if (state.password != state.confirmPassword) {
            emitError("Passwords do not match")
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                val result = authRepository.register(state.email.trim(), state.password)
                _uiState.update { it.copy(isLoading = false) }
                when (result) {
                    is AuthResult.Success ->
                        _navEvents.tryEmit(RegisterNavEvent.Success(result.email))
                    is AuthResult.VerificationRequired ->
                        _navEvents.tryEmit(RegisterNavEvent.VerificationRequired(result.email))
                }
            } catch (e: AdminLoginNotSupportedException) {
                emitError(ADMIN_LOGIN_USER_MESSAGE)
            } catch (e: ApiException) {
                val message = when (e.code) {
                    409 -> "An account with this email already exists"
                    else -> "Registration failed: ${e.message}"
                }
                emitError(message)
            } catch (e: Exception) {
                emitError("Registration failed: ${e.message}")
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
                        _navEvents.tryEmit(RegisterNavEvent.Success(result.email))
                    is AuthResult.VerificationRequired ->
                        _navEvents.tryEmit(RegisterNavEvent.VerificationRequired(result.email))
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
