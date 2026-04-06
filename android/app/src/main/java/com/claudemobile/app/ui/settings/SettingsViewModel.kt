package com.claudemobile.app.ui.settings

import androidx.lifecycle.ViewModel
import com.claudemobile.app.data.repository.AuthRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject

data class SettingsUiState(
    val serverUrl: String = "",
    val username: String = "",
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(
        SettingsUiState(
            serverUrl = authRepository.getServerUrl() ?: "",
            username = authRepository.getUsername() ?: "",
        )
    )
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    fun logout() {
        authRepository.logout()
    }
}
