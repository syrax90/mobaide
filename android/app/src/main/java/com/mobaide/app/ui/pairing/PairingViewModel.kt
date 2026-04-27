package com.mobaide.app.ui.pairing

import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.QrPayload
import com.mobaide.app.data.repository.PairingRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import java.util.UUID
import javax.inject.Inject

data class PairingUiState(
    val isProcessing: Boolean = false,
    val error: String? = null,
    val pairingComplete: Boolean = false,
)

@HiltViewModel
class PairingViewModel @Inject constructor(
    private val pairingRepository: PairingRepository,
) : ViewModel() {

    private val json = Json { ignoreUnknownKeys = true }
    private var hasProcessed = false

    private val _uiState = MutableStateFlow(PairingUiState())
    val uiState: StateFlow<PairingUiState> = _uiState.asStateFlow()

    fun onQrDetected(rawValue: String) {
        if (hasProcessed || _uiState.value.isProcessing) return

        val payload = try {
            json.decodeFromString<QrPayload>(rawValue)
        } catch (_: Exception) {
            return // Not a valid JSON — keep scanning
        }

        if (payload.type != "claude-pair") return

        hasProcessed = true
        completePairing(payload.server, payload.token, payload.agent_id)
    }

    fun pairManually(serverUrl: String, token: String, agentId: String) {
        if (_uiState.value.isProcessing) return
        hasProcessed = true
        completePairing(serverUrl, token, agentId)
    }

    fun clearError() {
        _uiState.update { it.copy(error = null) }
    }

    private fun completePairing(serverUrl: String, token: String, agentId: String) {
        viewModelScope.launch {
            _uiState.update { it.copy(isProcessing = true, error = null) }
            try {
                val deviceId = UUID.randomUUID().toString()
                val deviceName = "${Build.MANUFACTURER} ${Build.MODEL}"

                val response = pairingRepository.completePairing(
                    serverUrl = serverUrl,
                    pairingToken = token,
                    deviceId = deviceId,
                    deviceName = deviceName,
                )

                pairingRepository.savePairingCredentials(
                    serverUrl = serverUrl,
                    response = response,
                    deviceId = deviceId,
                )

                _uiState.update { it.copy(isProcessing = false, pairingComplete = true) }
            } catch (e: ApiException) {
                hasProcessed = false
                val msg = when (e.code) {
                    400 -> "Invalid or expired pairing code"
                    else -> "Pairing failed: ${e.errorBody}"
                }
                _uiState.update { it.copy(isProcessing = false, error = msg) }
            } catch (e: Exception) {
                hasProcessed = false
                _uiState.update {
                    it.copy(isProcessing = false, error = "Connection failed: ${e.message}")
                }
            }
        }
    }
}
