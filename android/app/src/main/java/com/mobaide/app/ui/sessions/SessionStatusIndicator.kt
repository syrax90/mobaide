package com.mobaide.app.ui.sessions

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.common.pixel.PixelStatusDot
import com.mobaide.app.ui.common.pixel.PixelWorkingSquare
import com.mobaide.app.ui.theme.MobaideTheme
import com.mobaide.app.ui.theme.pixel

const val SESSION_WORKING_INDICATOR_TAG = "session_working_indicator"
const val SESSION_STATUS_SQUARE_TAG = "session_status_square"

/**
 * A session row's leading mark: the walking pixel square while the agent
 * works, otherwise the static status square. Decorative — the chip next to the
 * row carries the words.
 */
@Composable
fun SessionStatusIndicator(session: SessionResponse, modifier: Modifier = Modifier) {
    if (isSessionWorking(session)) {
        PixelWorkingSquare(
            color = MaterialTheme.pixel.accent,
            modifier = modifier.testTag(SESSION_WORKING_INDICATOR_TAG),
        )
    } else {
        PixelStatusDot(
            color = MaterialTheme.pixel.sessionStateColor(session.state),
            modifier = modifier.testTag(SESSION_STATUS_SQUARE_TAG),
        )
    }
}

private val previewSessions = listOf(
    SessionResponse(session_id = "a", state = "running", created_at = "", activity = "working"),
    SessionResponse(session_id = "b", state = "running", created_at = "", activity = "idle"),
    SessionResponse(session_id = "c", state = "starting", created_at = ""),
    SessionResponse(session_id = "d", state = "crashed", created_at = ""),
)

@Composable
private fun SessionStatusIndicatorPreviewContent() {
    Row(
        modifier = Modifier.padding(16.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        previewSessions.forEach { SessionStatusIndicator(it) }
    }
}

@Preview(name = "Session status dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun SessionStatusIndicatorPreviewDark() {
    MobaideTheme(darkTheme = true) { SessionStatusIndicatorPreviewContent() }
}

@Preview(name = "Session status light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun SessionStatusIndicatorPreviewLight() {
    MobaideTheme(darkTheme = false) { SessionStatusIndicatorPreviewContent() }
}
