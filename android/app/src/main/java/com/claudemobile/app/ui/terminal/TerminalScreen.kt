package com.claudemobile.app.ui.terminal

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.claudemobile.app.ui.theme.StateCrashed
import com.claudemobile.app.ui.theme.StateRunning
import com.claudemobile.app.ui.theme.StateStarting
import com.claudemobile.app.ui.theme.StateStopped
import com.claudemobile.app.ui.theme.StateStopping
import com.claudemobile.app.ui.theme.TerminalBackground

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TerminalScreen(
    viewModel: TerminalViewModel,
    onBack: () -> Unit,
) {
    val sessionState by viewModel.sessionState.collectAsStateWithLifecycle()
    val isConnected by viewModel.isConnected.collectAsStateWithLifecycle()
    val connectionState by viewModel.connectionState.collectAsStateWithLifecycle()
    val promptState by viewModel.promptState.collectAsStateWithLifecycle()
    var inputText by remember { mutableStateOf(TextFieldValue("")) }
    var requestInputFocus by remember { mutableStateOf(false) }
    var pendingClear by remember { mutableStateOf(false) }

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
            BoxWithConstraints(modifier = Modifier.weight(1f)) {
                Box(modifier = Modifier.fillMaxSize().background(TerminalBackground)) {
                    TerminalSurfaceView(
                        session = viewModel.session,
                        sessionCallback = viewModel.sessionCallback,
                        onResize = { rows, cols ->
                            if (isConnected) viewModel.resize(rows, cols)
                        },
                        onRowTapped = { text, isCurrentInput ->
                            if (isCurrentInput) {
                                // Mark that the PTY buffer holds stale text that must be
                                // cleared atomically when the user presses Send.
                                pendingClear = true
                            }
                            inputText = TextFieldValue(text = text, selection = TextRange(text.length))
                            requestInputFocus = true
                        },
                        modifier = Modifier.fillMaxSize(),
                    )
                    ConnectionOverlay(state = connectionState)
                }
            }
            InputBar(
                text = inputText,
                onTextChange = { inputText = it },
                onSend = { text ->
                    viewModel.sendInput(text, clearFirst = pendingClear)
                    pendingClear = false
                },
                onSendBytes = viewModel::sendBytes,
                enabled = isConnected && sessionState == "running",
                requestFocus = requestInputFocus,
                onFocusHandled = { requestInputFocus = false },
            )
        }
    }

    // ── Prompt detection bottom sheets ───────────────────────────────────────────
    when (val prompt = promptState) {
        is PromptState.Checkbox -> CheckboxPromptSheet(
            state = prompt,
            onMoveUp = { viewModel.sendBytes(InkSequenceBuilder.arrowUp()) },
            onMoveDown = { viewModel.sendBytes(InkSequenceBuilder.arrowDown()) },
            onToggle = { viewModel.sendBytes(InkSequenceBuilder.checkboxToggle()) },
            onSubmit = { viewModel.sendBytes(InkSequenceBuilder.checkboxSubmit()) },
            onDismiss = { viewModel.dismissPrompt() },
        )
        is PromptState.TextInput -> TextInputPromptSheet(
            state = prompt,
            onSubmit = { text -> viewModel.sendBytes(InkSequenceBuilder.textInput(text)) },
            onDismiss = { viewModel.dismissPrompt() },
        )
        PromptState.None -> { /* no sheet */ }
    }
}

@Composable
private fun ConnectionOverlay(state: ConnectionState) {
    if (state == ConnectionState.Live) return
    val (label, bg) = when (state) {
        ConnectionState.Reconnecting -> "Reconnecting\u2026" to Color(0xCC202020)
        ConnectionState.ReplayingBuffer -> "Replaying buffer\u2026" to Color(0xCC2A4D6E)
        ConnectionState.Live -> return
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        contentAlignment = Alignment.TopCenter,
    ) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(50))
                .background(bg)
                .padding(horizontal = 16.dp, vertical = 6.dp),
        ) {
            Text(
                text = label,
                color = Color.White,
                style = MaterialTheme.typography.labelMedium.copy(
                    fontFamily = FontFamily.Monospace,
                ),
            )
        }
    }
}
