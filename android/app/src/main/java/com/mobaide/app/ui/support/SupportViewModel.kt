package com.mobaide.app.ui.support

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.UserIdentityStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class SupportUiState(
    val deviceId: String = "",
    val deviceName: String = "",
    val userEmail: String? = null,
)

@HiltViewModel
class SupportViewModel @Inject constructor(
    private val deviceIdentityStore: DeviceIdentityStore,
    private val userIdentityStore: UserIdentityStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(SupportUiState())
    val uiState: StateFlow<SupportUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            val deviceId = deviceIdentityStore.getOrCreateDeviceId()
            _uiState.update {
                it.copy(
                    deviceId = deviceId,
                    deviceName = deviceIdentityStore.getDeviceName(),
                    userEmail = userIdentityStore.getUserEmail(),
                )
            }
        }
    }
}
