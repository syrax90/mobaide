package com.mobaide.app.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.GoogleAuthResult
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MAX
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_MIN
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.DeleteAccountResult
import com.mobaide.app.data.repository.DeletionMode
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SettingsUiState(
    val userEmail: String = "",
    val terminalDisplayMode: TerminalDisplayMode = TerminalDisplayMode.Full,
    val terminalStaticColumns: Int = TERMINAL_STATIC_COLUMNS_DEFAULT,
    val deleteDialog: DeleteAccountDialogState = DeleteAccountDialogState.Hidden,
)

/**
 * Multi-step state machine for the Danger-zone "Delete account" flow.
 *
 * Hidden → Explain (user picks mode + re-auth method) → ReAuth* (current
 * password OR fresh Google ID token) → ConfirmEmail (must type exact email)
 * → submit → server returns 204 → SessionManager receives WS-4010 → app
 * routes to login automatically. Errors at any step set [errorMessage] and
 * stay on the current step.
 *
 * The [mode] field (Scheduled vs Immediate) is chosen on the Explain step
 * and threaded through every later state so the submit-button label can
 * adapt and the final API call carries the right value.
 */
sealed interface DeleteAccountDialogState {
    data object Hidden : DeleteAccountDialogState
    data class Explain(
        val email: String,
        val mode: DeletionMode = DeletionMode.Scheduled,
    ) : DeleteAccountDialogState
    data class ReAuthPassword(
        val email: String,
        val mode: DeletionMode,
        val password: String = "",
        val errorMessage: String? = null,
        val submitting: Boolean = false,
    ) : DeleteAccountDialogState
    data class ReAuthGoogle(
        val email: String,
        val mode: DeletionMode,
        val errorMessage: String? = null,
        val submitting: Boolean = false,
    ) : DeleteAccountDialogState
    data class ConfirmEmail(
        val email: String,
        val mode: DeletionMode,
        val typedEmail: String = "",
        val idToken: String? = null,
        val nonce: String? = null,
        val password: String? = null,
        val errorMessage: String? = null,
        val submitting: Boolean = false,
    ) : DeleteAccountDialogState
}

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val preferencesStore: PreferencesStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        SettingsUiState(
            userEmail = authRepository.getUserEmail() ?: "",
        ),
    )
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            preferencesStore.terminalDisplayMode.collect { mode ->
                _uiState.update { it.copy(terminalDisplayMode = mode) }
            }
        }
        viewModelScope.launch {
            preferencesStore.terminalStaticColumns.collect { cols ->
                _uiState.update { it.copy(terminalStaticColumns = cols) }
            }
        }
    }

    fun setTerminalDisplayMode(mode: TerminalDisplayMode) {
        viewModelScope.launch { preferencesStore.setTerminalDisplayMode(mode) }
    }

    fun setTerminalStaticColumns(columns: Int) {
        val clamped = columns.coerceIn(TERMINAL_STATIC_COLUMNS_MIN, TERMINAL_STATIC_COLUMNS_MAX)
        viewModelScope.launch { preferencesStore.setTerminalStaticColumns(clamped) }
    }

    fun logout() {
        authRepository.userLogout()
    }

    fun startDeleteAccount() {
        val email = authRepository.getUserEmail() ?: return
        _uiState.update { it.copy(deleteDialog = DeleteAccountDialogState.Explain(email)) }
    }

    fun dismissDeleteDialog() {
        _uiState.update { it.copy(deleteDialog = DeleteAccountDialogState.Hidden) }
    }

    /**
     * Update the chosen deletion mode on the Explain step. Selection is
     * threaded into every subsequent state via [chooseReAuthMethod].
     */
    fun setDeletionMode(mode: DeletionMode) {
        val state = _uiState.value.deleteDialog
        if (state is DeleteAccountDialogState.Explain) {
            _uiState.update { it.copy(deleteDialog = state.copy(mode = mode)) }
        }
    }

    /**
     * Advance from Explain → ReAuth*. The user already picked the deletion
     * mode on the Explain step; we carry it through every subsequent state.
     * Both re-auth options are shown — the unsupported one (e.g. Google for
     * a password-only account) surfaces an inline error on submission rather
     * than being grayed out client-side, since the server is the source of
     * truth on which methods are actually available.
     */
    fun chooseReAuthMethod(useGoogle: Boolean) {
        val state = _uiState.value.deleteDialog as? DeleteAccountDialogState.Explain
            ?: return
        _uiState.update {
            it.copy(
                deleteDialog = if (useGoogle) {
                    DeleteAccountDialogState.ReAuthGoogle(state.email, state.mode)
                } else {
                    DeleteAccountDialogState.ReAuthPassword(state.email, state.mode)
                },
            )
        }
    }

    fun onPasswordChange(password: String) {
        val state = _uiState.value.deleteDialog
        if (state is DeleteAccountDialogState.ReAuthPassword) {
            _uiState.update { it.copy(deleteDialog = state.copy(password = password)) }
        }
    }

    fun confirmPasswordReauth() {
        val state = _uiState.value.deleteDialog
        if (state !is DeleteAccountDialogState.ReAuthPassword) return
        if (state.password.isBlank()) {
            _uiState.update {
                it.copy(
                    deleteDialog = state.copy(errorMessage = "Enter your password."),
                )
            }
            return
        }
        _uiState.update {
            it.copy(
                deleteDialog = DeleteAccountDialogState.ConfirmEmail(
                    email = state.email,
                    mode = state.mode,
                    password = state.password,
                ),
            )
        }
    }

    fun onGoogleAuthResult(result: GoogleAuthResult) {
        val state = _uiState.value.deleteDialog
        if (state !is DeleteAccountDialogState.ReAuthGoogle) return
        when (result) {
            is GoogleAuthResult.Success -> _uiState.update {
                it.copy(
                    deleteDialog = DeleteAccountDialogState.ConfirmEmail(
                        email = state.email,
                        mode = state.mode,
                        idToken = result.idToken,
                        nonce = result.nonce,
                    ),
                )
            }
            is GoogleAuthResult.Cancelled -> _uiState.update {
                it.copy(
                    deleteDialog = state.copy(
                        submitting = false,
                        errorMessage = "Sign-in cancelled.",
                    ),
                )
            }
            is GoogleAuthResult.NotConfigured -> _uiState.update {
                it.copy(
                    deleteDialog = state.copy(
                        submitting = false,
                        errorMessage = "Google Sign-In is not configured in this build.",
                    ),
                )
            }
            is GoogleAuthResult.NoAccounts -> _uiState.update {
                it.copy(
                    deleteDialog = state.copy(
                        submitting = false,
                        errorMessage = result.message,
                    ),
                )
            }
            is GoogleAuthResult.PlayServicesUnavailable -> _uiState.update {
                it.copy(
                    deleteDialog = state.copy(
                        submitting = false,
                        errorMessage = "Google Play services is missing or out of date.",
                    ),
                )
            }
            is GoogleAuthResult.Failed -> _uiState.update {
                it.copy(
                    deleteDialog = state.copy(
                        submitting = false,
                        errorMessage = result.message,
                    ),
                )
            }
        }
    }

    fun markGoogleReauthSubmitting() {
        val state = _uiState.value.deleteDialog
        if (state is DeleteAccountDialogState.ReAuthGoogle) {
            _uiState.update {
                it.copy(deleteDialog = state.copy(submitting = true, errorMessage = null))
            }
        }
    }

    fun onTypedEmailChange(typedEmail: String) {
        val state = _uiState.value.deleteDialog
        if (state is DeleteAccountDialogState.ConfirmEmail) {
            _uiState.update { it.copy(deleteDialog = state.copy(typedEmail = typedEmail)) }
        }
    }

    fun submitDelete(onSubmitted: (DeletionMode) -> Unit) {
        val state = _uiState.value.deleteDialog
        if (state !is DeleteAccountDialogState.ConfirmEmail) return
        if (state.typedEmail != state.email) {
            _uiState.update {
                it.copy(
                    deleteDialog = state.copy(
                        errorMessage = "Type your email exactly to confirm.",
                    ),
                )
            }
            return
        }
        _uiState.update {
            it.copy(deleteDialog = state.copy(submitting = true, errorMessage = null))
        }
        viewModelScope.launch {
            val outcome = try {
                if (state.idToken != null && state.nonce != null) {
                    authRepository.deleteAccountWithGoogle(
                        state.idToken, state.nonce, state.mode,
                    )
                } else if (state.password != null) {
                    authRepository.deleteAccountWithPassword(state.password, state.mode)
                } else {
                    DeleteAccountResult.ReauthFailed("Re-authentication required.")
                }
            } catch (e: Exception) {
                DeleteAccountResult.ReauthFailed(e.message ?: "Network error")
            }
            when (outcome) {
                is DeleteAccountResult.Submitted -> {
                    // Server returns 204 and follows up with a best-effort
                    // WS-4010 close that would normally drive
                    // SessionManager.AccountRemoved. Don't rely on that frame
                    // alone: a backgrounded app or a network blip can drop it
                    // and leave the local session looking authenticated with
                    // a JWT the server has already revoked. Clear local state
                    // explicitly; SessionManager.invalidateUserSession is
                    // idempotent so a delayed 4010 is a harmless no-op.
                    authRepository.userLogout()
                    onSubmitted(outcome.mode)
                    _uiState.update { it.copy(deleteDialog = DeleteAccountDialogState.Hidden) }
                }
                DeleteAccountResult.Forbidden -> _uiState.update {
                    it.copy(
                        deleteDialog = state.copy(
                            submitting = false,
                            errorMessage = "This account cannot be self-deleted (seed admin).",
                        ),
                    )
                }
                is DeleteAccountResult.ReauthFailed -> _uiState.update {
                    it.copy(
                        deleteDialog = state.copy(
                            submitting = false,
                            errorMessage = outcome.message,
                        ),
                    )
                }
            }
        }
    }
}
