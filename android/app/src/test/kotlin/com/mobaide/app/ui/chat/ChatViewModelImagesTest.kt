package com.mobaide.app.ui.chat

import android.graphics.Bitmap
import android.net.Uri
import app.cash.turbine.test
import androidx.compose.ui.text.input.TextFieldValue
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.chat.ChatImageEncoder
import com.mobaide.app.data.chat.ChatImagePayload
import com.mobaide.app.data.chat.ChatProtocol
import com.mobaide.app.data.chat.ChatSessionCache
import com.mobaide.app.data.chat.ChatStatus
import com.mobaide.app.data.chat.EncodedChatImage
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Picked images in [ChatViewModel]: while a batch is still being read, Send and a
 * second pick do nothing, so a message never leaves with only part of the batch.
 * The encoder holds the second image on a gate.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatViewModelImagesTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val socketEvents = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val sent = mutableListOf<JsonObject>()

    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns socketEvents
        every { sendBinary(any()) } answers {
            sent += Json.parseToJsonElement(firstArg<ByteString>().substring(1).utf8()).jsonObject
            true
        }
    }

    private val session = SessionResponse(session_id = "chat-1", state = "running", created_at = "2026-11-08T00:00:00Z")

    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("chat-1") } returns connection
        every { observeSessionList() } returns MutableStateFlow(listOf(session))
        every { observeSessionsSynced() } returns MutableStateFlow(true)
        coEvery { listSessions() } returns listOf(session)
    }

    private val chatCache = mockk<ChatSessionCache>(relaxed = true).also { cache ->
        every { cache.get(any()) } returns null
    }

    private val firstImage = Uri.parse("content://media/1")
    private val secondImage = Uri.parse("content://media/2")
    private val thirdImage = Uri.parse("content://media/3")
    private val secondRead = CompletableDeferred<Unit>()

    /** Each image's payload data is its Uri's last segment, so a prompt shows which images it carries. */
    private val imageEncoder = mockk<ChatImageEncoder> {
        coEvery { encode(any()) } coAnswers {
            val uri = firstArg<Uri>()
            if (uri == secondImage) secondRead.await()
            EncodedChatImage(
                ChatImagePayload("image/jpeg", uri.lastPathSegment!!),
                Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888),
            )
        }
    }

    private fun createViewModel(): ChatViewModel =
        ChatViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "chat-1")),
            sessionRepository = sessionRepository,
            customAgentTypesRepository = mockk { every { observeTypes() } returns MutableStateFlow(emptyList()) },
            templatesRepository = mockk(relaxed = true),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            attentionNotifier = mockk(relaxed = true),
            chatCache = chatCache,
            imageEncoder = imageEncoder,
            reviewPromptCoordinator = mockk(relaxed = true),
            preferencesStore = mockk { every { chatFocusView } returns flowOf(true) },
        )

    private suspend fun openReady(): ChatViewModel {
        val vm = createViewModel()
        socketEvents.emit(WsEvent.Connected)
        val snapshot = """{"t":"snapshot","epoch":"e1","seq":1,"items":[],"state":{"status":"ready"}}"""
        socketEvents.emit(
            WsEvent.Binary(byteArrayOf(ChatProtocol.TYPE_FRAME).plus(snapshot.toByteArray(Charsets.UTF_8)).toByteString()),
        )
        vm.uiState.first { it.canAct && it.state.status == ChatStatus.READY }
        return vm
    }

    private fun prompts(): List<JsonObject> = sent.filter { it["op"]?.jsonPrimitive?.content == "prompt" }

    private fun imageData(prompt: JsonObject): List<String> =
        prompt["images"]?.jsonArray?.map { it.jsonObject["data"]!!.jsonPrimitive.content }.orEmpty()

    @Test
    fun sendPrompt_whileTheBatchIsHalfRead_sendsNothing_andTheNextSendCarriesTheWholeBatch() = runTest {
        val vm = openReady()
        vm.updateComposer(TextFieldValue("what is on these?"))
        vm.addImages(listOf(firstImage, secondImage))
        runCurrent()
        assertEquals(listOf("1"), vm.uiState.value.images.map { it.payload.data })
        assertTrue(vm.uiState.value.imagesLoading)

        vm.sendPrompt()

        assertTrue(prompts().isEmpty())
        assertEquals("what is on these?", vm.composer.value.text)

        secondRead.complete(Unit)
        runCurrent()
        assertFalse(vm.uiState.value.imagesLoading)
        vm.sendPrompt()

        val prompt = prompts().single()
        assertEquals("what is on these?", prompt["text"]!!.jsonPrimitive.content)
        assertEquals(listOf("1", "2"), imageData(prompt))
        assertTrue(vm.uiState.value.images.isEmpty())
    }

    @Test
    fun addImages_whenAReadNeverReturns_givesUpAfter120Seconds_andSendCarriesTheImagesRead() = runTest {
        val vm = openReady()
        vm.updateComposer(TextFieldValue("what is on these?"))

        vm.messages.test {
            // secondRead never completes: the read of the second image hangs.
            vm.addImages(listOf(firstImage, secondImage))
            runCurrent()
            advanceTimeBy(119_999)
            assertTrue(vm.uiState.value.imagesLoading)

            advanceTimeBy(1)
            runCurrent()

            assertEquals("Could not read the image", awaitItem())
        }
        assertFalse(vm.uiState.value.imagesLoading)
        assertEquals(listOf("1"), vm.uiState.value.images.map { it.payload.data })

        vm.sendPrompt()

        val prompt = prompts().single()
        assertEquals("what is on these?", prompt["text"]!!.jsonPrimitive.content)
        assertEquals(listOf("1"), imageData(prompt))
    }

    @Test
    fun addImages_whileABatchIsRead_isIgnored() = runTest {
        val vm = openReady()
        vm.addImages(listOf(firstImage, secondImage))
        runCurrent()

        vm.addImages(listOf(thirdImage))
        runCurrent()
        secondRead.complete(Unit)
        runCurrent()

        assertFalse(vm.uiState.value.imagesLoading)
        assertEquals(listOf("1", "2"), vm.uiState.value.images.map { it.payload.data })
    }
}
