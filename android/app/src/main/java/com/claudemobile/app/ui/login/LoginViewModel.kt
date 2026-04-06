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
    val serverUrl: String = "https://",
    val username: String = "",
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

    fun updateServerUrl(url: String) {
        _uiState.update { it.copy(serverUrl = url, error = null) }
    }

    fun updateUsername(username: String) {
        _uiState.update { it.copy(username = username, error = null) }
    }

    fun updatePassword(password: String) {
        _uiState.update { it.copy(password = password, error = null) }
    }

    fun login() {
        val state = _uiState.value
        if (state.serverUrl.isBlank() || state.username.isBlank() || state.password.isBlank()) {
            _uiState.update { it.copy(error = "All fields are required") }
            return
        }

        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                authRepository.login(state.serverUrl, state.username, state.password)
                _loginSuccess.value = true
            } catch (e: ApiException) {
                val message = when (e.code) {
                    401 -> "Invalid credentials"
                    else -> "Connection failed: ${e.message}"
                }
                _uiState.update { it.copy(isLoading = false, error = message) }
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(isLoading = false, error = "Connection failed: ${e.message}")
                }
            }
        }
    }
}
