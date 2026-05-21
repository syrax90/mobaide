package com.mobaide.app.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.repository.AuthRepository
import com.mobaide.app.data.repository.CancelDeletionResult
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * State for the soft-delete grace-window banner.
 *
 * [scheduledFor] is the ISO timestamp the server reported on login; when
 * non-null, the banner is visible. [errorMessage] surfaces a failed cancel
 * attempt (e.g., network blip).
 */
data class GraceBannerState(
    val scheduledFor: String? = null,
    val cancelling: Boolean = false,
    val errorMessage: String? = null,
)

@HiltViewModel
class GraceBannerViewModel @Inject constructor(
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(
        GraceBannerState(scheduledFor = authRepository.getScheduledDeletionAt())
    )
    val state: StateFlow<GraceBannerState> = _state.asStateFlow()

    fun cancel() {
        _state.update { it.copy(cancelling = true, errorMessage = null) }
        viewModelScope.launch {
            try {
                val outcome = authRepository.cancelAccountDeletion()
                when (outcome) {
                    CancelDeletionResult.Cancelled,
                    CancelDeletionResult.NotScheduled -> _state.update {
                        it.copy(scheduledFor = null, cancelling = false)
                    }
                }
            } catch (e: Exception) {
                _state.update {
                    it.copy(
                        cancelling = false,
                        errorMessage = e.message ?: "Network error",
                    )
                }
            }
        }
    }
}

/**
 * Persistent banner shown on authenticated landing screens when the user's
 * account is in the soft-delete grace window. Renders nothing if no
 * deletion is scheduled.
 *
 * **Design note — intentional divergence from the project's ViewModel
 * convention.** Every other screen-level composable in this app receives
 * its `ViewModel` as an explicit parameter, instantiated via
 * `hiltViewModel()` at the navigation layer in `AppNavigation.kt`. This
 * composable is the deliberate exception: it's a drop-in widget meant to
 * be slotted into multiple unrelated screens (currently `AgentListScreen`;
 * `SettingsScreen` is a likely future host) without forcing each host
 * screen to thread an extra VM parameter from its NavGraph entry. The
 * default `viewModel = hiltViewModel()` keeps callsites trivial; callers
 * that want to share a single instance (e.g., across screens that read
 * the same `scheduledDeletionAt`) can still pass an explicit instance.
 */
@Composable
fun GraceBannerSection(
    contentPadding: PaddingValues = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
    viewModel: GraceBannerViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val scheduledFor = state.scheduledFor ?: return

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(contentPadding)
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.errorContainer)
            .padding(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                imageVector = Icons.Default.Warning,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.error,
            )
            Spacer(modifier = Modifier.height(0.dp))
            Text(
                text = " Account scheduled for deletion",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
        }
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = "Final purge on $scheduledFor. Sign in cancels at any time during the grace window.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onErrorContainer,
        )
        val errorMessage = state.errorMessage
        if (errorMessage != null) {
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = errorMessage,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        Spacer(modifier = Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
            OutlinedButton(
                onClick = viewModel::cancel,
                enabled = !state.cancelling,
            ) {
                if (state.cancelling) {
                    CircularProgressIndicator(
                        modifier = Modifier.height(16.dp),
                        strokeWidth = 2.dp,
                    )
                } else {
                    Text("Cancel scheduled deletion")
                }
            }
        }
    }
}
