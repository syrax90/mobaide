package com.mobaide.app.ui.devices

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.network.dto.DeviceResponse
import com.mobaide.app.data.repository.DeviceRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class DevicesUiState(
    val devices: List<DeviceResponse> = emptyList(),
    val currentDeviceId: String? = null,
    val isLoading: Boolean = false,
    val error: String? = null,
    val revokeTarget: DeviceResponse? = null,
)

@HiltViewModel
class DevicesViewModel @Inject constructor(
    private val deviceRepository: DeviceRepository,
    private val deviceIdentityStore: DeviceIdentityStore,
) : ViewModel() {

    private val _uiState = MutableStateFlow(DevicesUiState())
    val uiState: StateFlow<DevicesUiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            _uiState.update {
                it.copy(currentDeviceId = deviceIdentityStore.getOrCreateDeviceId())
            }
        }
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _uiState.update { it.copy(isLoading = true, error = null) }
            try {
                val devices = deviceRepository.listDevices()
                _uiState.update { it.copy(devices = devices) }
            } catch (t: Throwable) {
                _uiState.update {
                    it.copy(error = t.message ?: "Failed to load devices")
                }
            } finally {
                _uiState.update { it.copy(isLoading = false) }
            }
        }
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    fun showRevokeConfirm(device: DeviceResponse) {
        _uiState.update { it.copy(revokeTarget = device) }
    }

    fun dismissRevokeConfirm() {
        _uiState.update { it.copy(revokeTarget = null) }
    }

    fun confirmRevoke(device: DeviceResponse) {
        _uiState.update { it.copy(revokeTarget = null) }
        viewModelScope.launch {
            try {
                deviceRepository.revokeDevice(device.device_id)
                _uiState.update {
                    it.copy(devices = it.devices.filterNot { d -> d.device_id == device.device_id })
                }
            } catch (t: Throwable) {
                _uiState.update {
                    it.copy(error = t.message ?: "Failed to revoke device")
                }
            }
        }
    }
}
