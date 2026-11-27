package com.mobaide.app.ui.chat

import android.graphics.Bitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.mobaide.app.data.chat.ChatImagePayload
import com.mobaide.app.data.chat.ChatQueuedMessage
import com.mobaide.app.data.chat.ChatRequest
import com.mobaide.app.data.chat.ChatState
import com.mobaide.app.data.chat.ChatStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * [ChatUiState.promptSuggestion]: the agent's predicted next prompt shows only
 * while the chat can take it now. A tap fills the composer, so a chip offered
 * during a turn, a request card or a queued message would mislead the user.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatUiStatePromptSuggestionTest {

    private val suggested = listOf("Run the tests and fix what breaks", "Commit it")

    private fun ui(
        state: ChatState = ChatState(status = ChatStatus.READY, promptSuggestions = suggested),
        live: Boolean = true,
        sessionClosed: Boolean = false,
        images: List<ChatComposerImage> = emptyList(),
        imagesLoading: Boolean = false,
        restarting: Boolean = false,
        startingNew: Boolean = false,
    ) = ChatUiState(
        loaded = true,
        state = state,
        live = live,
        sessionClosed = sessionClosed,
        images = images,
        imagesLoading = imagesLoading,
        restarting = restarting,
        startingNew = startingNew,
    )

    private fun image() = ChatComposerImage(
        id = 1L,
        payload = ChatImagePayload("image/jpeg", "aGk="),
        thumbnail = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888).asImageBitmap(),
    )

    @Test
    fun aReadyLiveChat_offersTheMostLikelySuggestion() {
        assertEquals("Run the tests and fix what breaks", ui().promptSuggestion)
        assertNull(ui(state = ChatState(status = ChatStatus.READY)).promptSuggestion)
    }

    @Test
    fun noSuggestion_whenTheChatCannotTakeAPromptNow() {
        val ready = ChatState(status = ChatStatus.READY, promptSuggestions = suggested)
        val request = ChatRequest.Permission("r:1", null, "Edit main.py", "edit", null, emptyList(), emptyList())
        val blocked = mapOf(
            "running" to ui(state = ready.copy(status = ChatStatus.RUNNING)),
            "error" to ui(state = ready.copy(status = ChatStatus.ERROR)),
            "offline" to ui(live = false),
            "closed" to ui(sessionClosed = true),
            "request card" to ui(state = ready.copy(requests = listOf(request))),
            "queued message" to ui(state = ready.copy(queue = listOf(ChatQueuedMessage("q:1", "next", 0)))),
            "picked image" to ui(images = listOf(image())),
            "image being read" to ui(imagesLoading = true),
            "restarting" to ui(restarting = true),
            "starting a new conversation" to ui(startingNew = true),
        )

        for ((case, state) in blocked) {
            assertNull(case, state.promptSuggestion)
        }
    }
}
