package com.mobaide.app.ui.chat

import app.cash.turbine.test
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.chat.ChatProtocol
import com.mobaide.app.data.chat.ChatQueuedMessage
import com.mobaide.app.data.chat.ChatSessionCache
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
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Send now and remove on a queued message in [ChatViewModel]: a row with one in
 * flight takes no second command, and "already sent" is no user-facing error.
 * The socket is a fake that records `'c'` commands and pushes `'a'` frames.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatViewModelQueueTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val socketEvents = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val sent = mutableListOf<JsonObject>()
    private var socketAccepts = true

    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns socketEvents
        every { sendBinary(any()) } answers {
            if (socketAccepts) {
                sent += Json.parseToJsonElement(firstArg<ByteString>().substring(1).utf8()).jsonObject
            }
            socketAccepts
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
            imageEncoder = mockk(relaxed = true),
            reviewPromptCoordinator = mockk(relaxed = true),
        )

    private suspend fun emitFrame(json: String) {
        socketEvents.emit(
            WsEvent.Binary(byteArrayOf(ChatProtocol.TYPE_FRAME).plus(json.toByteArray(Charsets.UTF_8)).toByteString()),
        )
    }

    /** A running turn with one message waiting behind it. */
    private suspend fun openWithQueuedMessage(): ChatViewModel {
        val vm = createViewModel()
        socketEvents.emit(WsEvent.Connected)
        emitFrame(
            """{"t":"snapshot","epoch":"e1","seq":1,"items":[],"state":{"status":"running","steering":true,""" +
                """"queue":[{"id":"q:1","text":"also check the README","images":0}]}}""",
        )
        vm.uiState.first { it.live && it.state.queue.map(ChatQueuedMessage::id) == listOf("q:1") }
        return vm
    }

    private fun commands(op: String): List<JsonObject> = sent.filter { it["op"]?.jsonPrimitive?.content == op }

    private suspend fun answerLast(op: String, ok: Boolean, error: String? = null) {
        val id = commands(op).last()["id"]!!.jsonPrimitive.content
        val errorField = error?.let { ""","error":"$it"""" }.orEmpty()
        emitFrame("""{"t":"result","id":"$id","ok":$ok$errorField}""")
    }

    @Test
    fun steer_marksTheRowBusy_andTheRowTakesNoOtherCommandUntilTheResult() = runTest {
        val vm = openWithQueuedMessage()

        vm.steer("q:1")
        vm.steer("q:1")
        vm.dequeue("q:1")

        assertEquals(setOf("q:1"), vm.uiState.value.queueBusy)
        assertEquals(1, commands("steer").size)
        assertEquals(0, commands("dequeue").size)

        answerLast("steer", ok = true)
        vm.uiState.first { it.queueBusy.isEmpty() }
        vm.dequeue("q:1")

        assertEquals(1, commands("dequeue").size)
    }

    @Test
    fun steer_alreadySent_enablesTheRow_withoutAMessage() = runTest {
        val vm = openWithQueuedMessage()

        vm.messages.test {
            vm.steer("q:1")
            answerLast("steer", ok = false, error = "This message was already sent")
            vm.uiState.first { it.queueBusy.isEmpty() }
            runCurrent()

            expectNoEvents()
        }
    }

    @Test
    fun steer_otherFailure_showsTheAgentsError() = runTest {
        val vm = openWithQueuedMessage()

        vm.messages.test {
            vm.steer("q:1")
            answerLast("steer", ok = false, error = "This agent cannot take a message during a turn")

            assertEquals("This agent cannot take a message during a turn", awaitItem())
        }
        assertTrue(vm.uiState.value.queueBusy.isEmpty())
    }

    @Test
    fun steer_whenTheSocketTakesNoFrame_saysNotConnected_andLeavesTheRowEnabled() = runTest {
        val vm = openWithQueuedMessage()
        socketAccepts = false

        vm.messages.test {
            vm.steer("q:1")

            assertEquals("Not connected. Try again.", awaitItem())
        }
        assertTrue(vm.uiState.value.queueBusy.isEmpty())
    }

    @Test
    fun queueBusy_clearsWhenTheSocketDrops() = runTest {
        val vm = openWithQueuedMessage()
        vm.dequeue("q:1")
        assertEquals(setOf("q:1"), vm.uiState.value.queueBusy)

        socketEvents.emit(WsEvent.Disconnected)

        val ui = vm.uiState.first { !it.live }
        assertTrue(ui.queueBusy.isEmpty())
    }

    @Test
    fun queueBusy_clearsWhenTheRowLeavesTheQueue_beforeAnyResult() = runTest {
        val vm = openWithQueuedMessage()
        vm.steer("q:1")

        emitFrame("""{"t":"events","epoch":"e1","seq":2,"events":[{"seq":2,"type":"state","patch":{"queue":[]}}]}""")

        val ui = vm.uiState.first { it.state.queue.isEmpty() }
        assertTrue(ui.queueBusy.isEmpty())
    }
}
