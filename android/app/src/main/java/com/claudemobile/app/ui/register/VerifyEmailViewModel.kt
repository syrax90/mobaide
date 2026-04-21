package com.claudemobile.app.ui.register

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.repository.ADMIN_LOGIN_USER_MESSAGE
import com.claudemobile.app.data.repository.AdminLoginNotSupportedException
import com.claudemobile.app.data.repository.AuthRepository
import com.claudemobile.app.data.repository.ResendOutcome
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

data class VerifyEmailUiState(
    val email: String = "",
    val code: String = "",
    val isLoading: Boolean = false,
    val isResending: Boolean = false,
    val cooldownSeconds: Int = 0,
    val error: String? = null,
)

/** One-shot navigation events from the verify-email screen. */
sealed interface VerifyEmailNavEvent {
    data object Success : VerifyEmailNavEvent
}

@HiltViewModel
class VerifyEmailViewModel @Inject constructor(
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(VerifyEmailUiState())
    val uiState: StateFlow<VerifyEmailUiState> = _uiState.asStateFlow()

    private val _navEvents = MutableSharedFlow<VerifyEmailNavEvent>(extraBufferCapacity = 1)
    val navEvents: SharedFlow<VerifyEmailNavEvent> = _navEvents.asSharedFlow()

    private val _errorEvents = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val errorEvents: SharedFlow<String> = _errorEvents.asSharedFlow()

    private var cooldownJob: Job? = null

    fun setEmail(email: String) {
        _uiState.update { it.copy(email = email) }
    }

    fun updateCode(code: String) {
        // Only keep digits, cap at 6. This works both for direct keystrokes
        // and for paste: a full 6-digit paste lands as a single update that
        // survives the filter.
        val digits = code.filter(Char::isDigit).take(CODE_LENGTH)
        _uiState.update { it.copy(code = digits, error = null) }
    }

    fun submit() {
        val state = _uiState.value
        if (state.code.length != CODE_LENGTH) {
            emitError("Enter the $CODE_LENGTH-digit code we emailed you")
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                authRepository.verifyEmail(state.email, state.code)
                _uiState.update { it.copy(isLoading = false) }
                _navEvents.tryEmit(VerifyEmailNavEvent.Success)
            } catch (e: AdminLoginNotSupportedException) {
                emitError(ADMIN_LOGIN_USER_MESSAGE)
            } catch (e: ApiException) {
                emitError(friendlyApiMessage(e))
                _uiState.update { it.copy(code = "") }
            } catch (e: Exception) {
                emitError("Verification failed: ${e.message}")
            }
        }
    }

    fun resend() {
        if (_uiState.value.isResending || _uiState.value.cooldownSeconds > 0) return
        val email = _uiState.value.email
        if (email.isBlank()) return
        viewModelScope.launch {
            _uiState.update { it.copy(isResending = true, error = null) }
            try {
                when (val outcome = authRepository.resendVerification(email)) {
                    ResendOutcome.Sent -> {
                        _uiState.update { it.copy(isResending = false) }
                        startCooldown(DEFAULT_COOLDOWN_SECONDS)
                        _errorEvents.tryEmit("A new code is on its way.")
                    }
                    is ResendOutcome.RateLimited -> {
                        _uiState.update { it.copy(isResending = false) }
                        startCooldown(outcome.cooldownSecondsRemaining)
                        _errorEvents.tryEmit(
                            "Please wait ${outcome.cooldownSecondsRemaining}s before requesting another code.",
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

    private fun friendlyApiMessage(e: ApiException): String = when (e.code) {
        400 -> "That code isn't right. Try again."
        410 -> "That code expired. Tap \"Resend\" to get a new one."
        429 -> "Too many attempts. Request a new code."
        else -> "Verification failed: ${e.message}"
    }

    private fun emitError(message: String) {
        // Always clear loading so the submit button re-enables — mirrors
        // the pattern in RegisterViewModel / LoginViewModel. Every error
        // path in this VM must leave the UI in an actionable state.
        _uiState.update { it.copy(isLoading = false, error = message) }
        _errorEvents.tryEmit(message)
    }

    override fun onCleared() {
        cooldownJob?.cancel()
        super.onCleared()
    }

    private companion object {
        const val CODE_LENGTH = 6
        const val DEFAULT_COOLDOWN_SECONDS = 60
    }
}
