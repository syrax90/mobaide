package com.claudemobile.app.ui.terminal

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Circle
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.claudemobile.app.ui.theme.StateCrashed
import com.claudemobile.app.ui.theme.StateRunning
import com.claudemobile.app.ui.theme.StateStarting
import com.claudemobile.app.ui.theme.StateStopped
import com.claudemobile.app.ui.theme.StateStopping

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TerminalScreen(
    viewModel: TerminalViewModel,
    onBack: () -> Unit,
) {
    val sessionState by viewModel.sessionState.collectAsStateWithLifecycle()
    val isConnected by viewModel.isConnected.collectAsStateWithLifecycle()

    val stateColor = when (sessionState) {
        "running" -> StateRunning
        "starting" -> StateStarting
        "stopping" -> StateStopping
        "stopped" -> StateStopped
        "crashed" -> StateCrashed
        else -> StateStopped
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            Icons.Default.Circle,
                            contentDescription = sessionState,
                            tint = if (isConnected) stateColor else StateStopped,
                            modifier = Modifier.size(10.dp),
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = sessionState.replaceFirstChar { it.uppercase() },
                            style = MaterialTheme.typography.titleMedium.copy(
                                fontFamily = FontFamily.Monospace,
                            ),
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
                        )
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {
            TerminalView(
                lines = viewModel.lines,
                modifier = Modifier.weight(1f),
            )
            InputBar(
                onSend = viewModel::sendInput,
                enabled = isConnected && sessionState == "running",
            )
        }
    }
}
