package com.mobaide.app.ui.autoscan

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.wrapContentHeight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTopBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

import com.mobaide.app.ui.theme.StateCrashed
import com.mobaide.app.ui.theme.StateRunning
import com.mobaide.app.ui.theme.StateStarting
import com.mobaide.app.ui.theme.StateStopped

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AutoScanDetailScreen(
    projectName: String,
    onBack: () -> Unit,
    viewModel: AutoScanDetailViewModel,
) {
    val ui by viewModel.uiState.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(viewModel) {
        viewModel.errors.collect { msg ->
            snackbarHostState.showSnackbar(msg)
        }
    }

    PixelScaffold(
        snackbarHost = { PixelSnackbarHost(snackbarHostState) },
        topBar = {
            PixelTopBar(
                title = { Text(projectName) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Default.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier.fillMaxSize().padding(padding),
        ) {
            HeaderCard(
                statusText = ui.instance?.status ?: ui.connection.name,
                nextScanAtIso = ui.nextScanAt,
                cycleNumber = ui.cycleNumber,
                onRunNow = viewModel::runNow,
                onStop = viewModel::stop,
            )

            PhaseTimeline(
                phases = ui.phases,
                activePhase = ui.activePhase,
                recentIssues = ui.recentIssues,
            )

            LogPane(
                lines = ui.logLines,
                isPaused = ui.isPausedAutoscroll,
                newLineCount = ui.newLineCount,
                onPaused = viewModel::setPaused,
                onResume = viewModel::resumeAutoscroll,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Composable
private fun HeaderCard(
    statusText: String,
    nextScanAtIso: String?,
    cycleNumber: Int,
    onRunNow: () -> Unit,
    onStop: () -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(12.dp),
        colors = CardDefaults.cardColors(),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(statusText)
                Text(
                    text = statusText.replaceFirstChar { it.titlecase() },
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(start = 8.dp),
                )
                Text(
                    text = "  · cycle #$cycleNumber",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            CountdownText(
                nextScanAtIso = nextScanAtIso,
                modifier = Modifier.padding(top = 6.dp),
                style = MaterialTheme.typography.bodyMedium,
            )
            Row(
                modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                PixelButton(onClick = onRunNow, style = PixelButtonStyle.Primary) {
                    Icon(Icons.Default.PlayArrow, contentDescription = null)
                    Text("Run Now", modifier = Modifier.padding(start = 4.dp))
                }
                PixelButton(onClick = onStop) {
                    Icon(Icons.Default.Stop, contentDescription = null)
                    Text("Stop", modifier = Modifier.padding(start = 4.dp))
                }
            }
        }
    }
}

@Composable
private fun StatusDot(status: String) {
    val color = when (status.lowercase()) {
        "scanning", "running", "connected" -> StateRunning
        "idle", "connecting", "replaying" -> StateStarting
        "error", "failed", "disconnected" -> StateCrashed
        "stopped" -> StateStopped
        else -> StateStopped
    }
    Box(
        modifier = Modifier
            .size(10.dp)
            .background(color, RoundedCornerShape(50)),
    )
}

@Composable
private fun PhaseTimeline(
    phases: Map<String, AutoScanDetailViewModel.PhaseState>,
    activePhase: String?,
    recentIssues: List<AutoScanDetailViewModel.IssueRow>,
) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
    ) {
        Column(modifier = Modifier.padding(8.dp)) {
            PHASE_ORDER.forEach { phase ->
                val state = phases[phase] ?: AutoScanDetailViewModel.PhaseState()
                val isActive = phase == activePhase
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(vertical = 2.dp),
                ) {
                    Icon(
                        Icons.Default.KeyboardArrowDown,
                        contentDescription = null,
                        tint = phaseColor(state.status),
                    )
                    Text(
                        text = phase.replaceFirstChar { it.titlecase() },
                        style = if (isActive) MaterialTheme.typography.bodyLarge
                                else MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.padding(start = 4.dp),
                    )
                    Text(
                        text = "  ${state.status}",
                        style = MaterialTheme.typography.bodySmall,
                        color = phaseColor(state.status),
                    )
                }
            }
            if (recentIssues.isNotEmpty()) {
                Text(
                    text = "Recent issues:",
                    style = MaterialTheme.typography.labelMedium,
                    modifier = Modifier.padding(top = 6.dp),
                )
                recentIssues.takeLast(5).forEach { row ->
                    Text(
                        text = "${row.identifier}: ${row.title} (${row.step}/${row.status})",
                        style = MaterialTheme.typography.bodySmall,
                        color = phaseColor(row.status),
                    )
                }
            }
        }
    }
}

private fun phaseColor(status: String): Color = when (status) {
    "active" -> StateStarting
    "ok" -> StateRunning
    "failed" -> StateCrashed
    "skipped" -> StateStopped
    else -> StateStopped
}

@Composable
private fun LogPane(
    lines: List<AutoScanDetailViewModel.LogLine>,
    isPaused: Boolean,
    newLineCount: Int,
    onPaused: (Boolean) -> Unit,
    onResume: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()

    val isScrolledFromBottom by remember {
        derivedStateOf {
            val total = listState.layoutInfo.totalItemsCount
            val lastVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1
            total > 0 && lastVisible < total - 2
        }
    }
    LaunchedEffect(isScrolledFromBottom) {
        onPaused(isScrolledFromBottom)
    }

    LaunchedEffect(lines.size) {
        if (!isPaused && lines.isNotEmpty()) {
            listState.animateScrollToItem(lines.size - 1)
        }
    }

    Box(modifier = modifier) {
        Surface(
            color = Color(0xFF111111),
            modifier = Modifier.fillMaxSize(),
        ) {
            LazyColumn(
                state = listState,
                contentPadding = PaddingValues(8.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(lines, key = { it.id }) { line ->
                    Text(
                        text = line.text,
                        color = Color(0xFFE0E0E0),
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
        }
        if (isPaused && newLineCount > 0) {
            ExtendedFloatingActionButton(
                onClick = onResume,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(16.dp),
            ) {
                Text("▼ $newLineCount new")
            }
        }
    }
}
