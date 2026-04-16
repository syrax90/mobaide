package com.claudemobile.app.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.repository.AuthRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
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
) : ViewModel() {

    private val _uiState = MutableStateFlow(LoginUiState())
    val uiState: StateFlow<LoginUiState> = _uiState.asStateFlow()

    private val _loginSuccess = MutableStateFlow(false)
    val loginSuccess: StateFlow<Boolean> = _loginSuccess.asStateFlow()

    fun updateEmail(email: String) {
        _uiState.update { it.copy(email = email, error = null) }
    }

    fun updatePassword(password: String) {
        _uiState.update { it.copy(password = password, error = null) }
    }

    fun loginWithPassword() {
        val state = _uiState.value
        if (state.email.isBlank() || state.password.isBlank()) {
            _uiState.update { it.copy(error = "Email and password are required") }
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                authRepository.loginWithPassword(state.email.trim(), state.password)
                _loginSuccess.value = true
            } catch (e: ApiException) {
                val message = when (e.code) {
                    401 -> "Invalid email or password"
                    else -> "Login failed: ${e.message}"
                }
                _uiState.update { it.copy(isLoading = false, error = message) }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoading = false, error = "Login failed: ${e.message}")
                }
            }
        }
    }

    fun onGoogleIdToken(idToken: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                authRepository.loginWithGoogle(idToken)
                _loginSuccess.value = true
            } catch (e: ApiException) {
                _uiState.update {
                    it.copy(isLoading = false, error = "Google Sign-In failed: ${e.message}")
                }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoading = false, error = "Google Sign-In failed: ${e.message}")
                }
            }
        }
    }

    fun setError(message: String) {
        _uiState.update { it.copy(error = message, isLoading = false) }
    }
}
