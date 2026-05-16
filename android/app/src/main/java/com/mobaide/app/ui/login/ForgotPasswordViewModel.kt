package com.mobaide.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.PasswordResetOutcome
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

data class ForgotPasswordUiState(
    val email: String = "",
    val isSending: Boolean = false,
    val cooldownSeconds: Int = 0,
    val error: String? = null,
)

sealed interface ForgotPasswordNavEvent {
    data class CodeSent(val email: String) : ForgotPasswordNavEvent
}

@HiltViewModel
class ForgotPasswordViewModel @Inject constructor(
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ForgotPasswordUiState())
    val uiState: StateFlow<ForgotPasswordUiState> = _uiState.asStateFlow()

    private val _navEvents = MutableSharedFlow<ForgotPasswordNavEvent>(extraBufferCapacity = 1)
    val navEvents: SharedFlow<ForgotPasswordNavEvent> = _navEvents.asSharedFlow()

    private val _errorEvents = MutableSharedFlow<String>(extraBufferCapacity = 1)
    val errorEvents: SharedFlow<String> = _errorEvents.asSharedFlow()

    private var cooldownJob: Job? = null

    fun updateEmail(email: String) {
        _uiState.update { it.copy(email = email, error = null) }
    }

    fun submit() {
        val state = _uiState.value
        if (state.isSending || state.cooldownSeconds > 0) return
        val trimmed = state.email.trim()
        if (trimmed.isBlank() || !trimmed.contains('@')) {
            emitError("Enter a valid email address")
            return
        }
        viewModelScope.launch {
            _uiState.update { it.copy(isSending = true, error = null) }
            try {
                when (val outcome = authRepository.requestPasswordReset(trimmed)) {
                    PasswordResetOutcome.Sent -> {
                        _uiState.update { it.copy(isSending = false) }
                        _errorEvents.tryEmit(GENERIC_SENT_MESSAGE)
                        _navEvents.tryEmit(ForgotPasswordNavEvent.CodeSent(trimmed))
                    }
                    is PasswordResetOutcome.RateLimited -> {
                        _uiState.update { it.copy(isSending = false) }
                        startCooldown(outcome.retryAfterSeconds)
                        _errorEvents.tryEmit(
                            "Too many requests from this network. Try again in ${outcome.retryAfterSeconds}s.",
                        )
                    }
                }
            } catch (e: ApiException) {
                emitError("Couldn't request a reset code: ${e.message}")
            } catch (e: Exception) {
                emitError("Couldn't request a reset code: ${e.message}")
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
        _uiState.update { it.copy(isSending = false, error = message) }
        _errorEvents.tryEmit(message)
    }

    override fun onCleared() {
        cooldownJob?.cancel()
        super.onCleared()
    }

    private companion object {
        // Always-shown message regardless of whether the email is registered;
        // server is enumeration-resistant so the client mustn't leak existence.
        const val GENERIC_SENT_MESSAGE =
            "If an account exists for that email, we sent a code."
    }
}
