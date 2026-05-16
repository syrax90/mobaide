package com.mobaide.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.PasswordResetOutcome
import com.mobaide.app.data.repository.ResetPasswordError
import com.mobaide.app.data.repository.ResetPasswordFailure
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class ResetPasswordUiState(
    val email: String = "",
    val code: String = "",
    val newPassword: String = "",
    val confirmPassword: String = "",
    val isLoading: Boolean = false,
    val isResending: Boolean = false,
    val cooldownSeconds: Int = 0,
    val error: String? = null,
)

sealed interface ResetPasswordNavEvent {
    data object Success : ResetPasswordNavEvent
}

@HiltViewModel
class ResetPasswordViewModel @Inject constructor(
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ResetPasswordUiState())
    val uiState: StateFlow<ResetPasswordUiState> = _uiState.asStateFlow()

    private val _navEvents = MutableSharedFlow<ResetPasswordNavEvent>(extraBufferCapacity = 1)
    val navEvents: SharedFlow<ResetPasswordNavEvent> = _navEvents.asSharedFlow()

    private val _errorEvents = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val errorEvents: SharedFlow<String> = _errorEvents.asSharedFlow()

    private var cooldownJob: Job? = null

    fun setEmail(email: String) {
        _uiState.update { it.copy(email = email) }
    }

    fun updateCode(code: String) {
        val digits = code.filter(Char::isDigit).take(CODE_LENGTH)
        _uiState.update { it.copy(code = digits, error = null) }
    }

    fun updateNewPassword(value: String) {
        _uiState.update { it.copy(newPassword = value, error = null) }
    }

    fun updateConfirmPassword(value: String) {
        _uiState.update { it.copy(confirmPassword = value, error = null) }
    }

    fun submit() {
        val state = _uiState.value
        if (state.isLoading) return
        if (state.code.length != CODE_LENGTH) {
            emitError("Enter the $CODE_LENGTH-digit code we emailed you")
            return
        }
        if (state.newPassword.length < MIN_PASSWORD_LENGTH) {
            emitError("Password must be at least $MIN_PASSWORD_LENGTH characters")
            return
        }
        if (state.newPassword != state.confirmPassword) {
            emitError("Passwords don't match")
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                authRepository.resetPassword(state.email, state.code, state.newPassword)
                _uiState.update { it.copy(isLoading = false) }
                _navEvents.tryEmit(ResetPasswordNavEvent.Success)
            } catch (e: ResetPasswordFailure) {
                _uiState.update { it.copy(isLoading = false) }
                when (val reason = e.reason) {
                    ResetPasswordError.InvalidCode -> {
                        emitError("That code isn't right. Try again.")
                        _uiState.update { it.copy(code = "") }
                    }
                    ResetPasswordError.ExpiredCode -> emitError(
                        "That code expired. Tap \"Resend\" to get a new one.",
                    )
                    ResetPasswordError.AttemptsExhausted -> emitError(
                        "Too many attempts. Request a new code.",
                    )
                    ResetPasswordError.SameAsOld -> emitError(
                        "New password must differ from your current password.",
                    )
                    is ResetPasswordError.IpRateLimited -> emitError(
                        "Too many requests from this network. Try again in ${reason.retryAfterSeconds}s.",
                    )
                    is ResetPasswordError.Unknown -> emitError(
                        "Couldn't reset password: ${reason.message.ifBlank { "Unknown error (${reason.code})" }}",
                    )
                }
            } catch (e: Exception) {
                emitError("Couldn't reset password: ${e.message}")
            }
        }
    }

    fun resend() {
        val state = _uiState.value
        if (state.isResending || state.cooldownSeconds > 0) return
        if (state.email.isBlank()) return
        viewModelScope.launch {
            _uiState.update { it.copy(isResending = true, error = null) }
            try {
                when (val outcome = authRepository.requestPasswordReset(state.email)) {
                    PasswordResetOutcome.Sent -> {
                        _uiState.update { it.copy(isResending = false) }
                        startCooldown(DEFAULT_COOLDOWN_SECONDS)
                        _errorEvents.tryEmit("A new code is on its way.")
                    }
                    is PasswordResetOutcome.RateLimited -> {
                        _uiState.update { it.copy(isResending = false) }
                        startCooldown(outcome.retryAfterSeconds)
                        _errorEvents.tryEmit(
                            "Please wait ${outcome.retryAfterSeconds}s before requesting another code.",
                        )
                    }
                }
            } catch (e: Exception) {
                _uiState.update { it.copy(isResending = false) }
                emitError("Couldn't request a new code: ${e.message}")
            }
        }
    }

    private fun startCooldown(seconds: Int) {
        cooldownJob?.cancel()
        if (seconds <= 0) return
        _uiState.update { it.copy(cooldownSeconds = seconds) }
        cooldownJob = viewModelScope.launch {
            var remaining = seconds
            while (remaining > 0) {
                delay(1000)
                remaining--
                _uiState.update { it.copy(cooldownSeconds = remaining) }
            }
        }
    }

    private fun emitError(message: String) {
        _uiState.update { it.copy(isLoading = false, error = message) }
        _errorEvents.tryEmit(message)
    }

    override fun onCleared() {
        cooldownJob?.cancel()
        super.onCleared()
    }

    private companion object {
        const val CODE_LENGTH = 6
        const val MIN_PASSWORD_LENGTH = 8
        const val DEFAULT_COOLDOWN_SECONDS = 60
    }
}
