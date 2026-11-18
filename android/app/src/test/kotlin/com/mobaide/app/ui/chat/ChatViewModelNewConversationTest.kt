package com.mobaide.app.ui.chat

import app.cash.turbine.test
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.chat.ChatProtocol
import com.mobaide.app.data.chat.ChatSessionCache
import com.mobaide.app.data.chat.ChatStatus
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
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
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
 * New conversation in [ChatViewModel]: only an agent that offers it gets the
 * command, never while it starts (the user is told), and a double tap sends
 * one command without a message.
 * The socket is a fake that records `'c'` commands and pushes `'a'` frames.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatViewModelNewConversationTest {

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

    private val session = SessionResponse(session_id = "chat-1", state = "running", created_at = "2026-11-18T00:00:00Z")

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
            preferencesStore = mockk { every { chatFocusView } returns flowOf(true) },
        )

    private suspend fun emitFrame(json: String) {
        socketEvents.emit(
            WsEvent.Binary(byteArrayOf(ChatProtocol.TYPE_FRAME).plus(json.toByteArray(Charsets.UTF_8)).toByteString()),
        )
    }

    /** [stateJson] is the snapshot's state object without its braces. */
    private suspend fun open(stateJson: String): ChatViewModel {
        val vm = createViewModel()
        socketEvents.emit(WsEvent.Connected)
        emitFrame("""{"t":"snapshot","epoch":"e1","seq":1,"items":[],"state":{$stateJson}}""")
        vm.uiState.first { it.live && it.state.status == ChatStatus.READY }
        return vm
    }

    private fun newConversations(): List<JsonObject> =
        sent.filter { it["op"]?.jsonPrimitive?.content == "new_conversation" }

    @Test
    fun aDoubleTap_sendsOneCommand_andAFailureShowsTheAgentsError_andAllowsATryAgain() = runTest {
        val vm = open(""""status":"ready","new_conversation":true""")

        vm.newConversation()
        vm.newConversation()

        assertEquals(1, newConversations().size)
        assertTrue(vm.uiState.value.startingNew)
        assertFalse(vm.uiState.value.canStartNew)

        vm.messages.test {
            val id = newConversations().single()["id"]!!.jsonPrimitive.content
            emitFrame("""{"t":"result","id":"$id","ok":false,"error":"The agent is already starting"}""")

            assertEquals("The agent is already starting", awaitItem())
        }
        vm.uiState.first { !it.startingNew }
        vm.newConversation()

        assertEquals(2, newConversations().size)
    }

    @Test
    fun anIdleChat_needsNoConfirm_butARunningTurnOrAQueueDoes() = runTest {
        val vm = open(""""status":"ready","new_conversation":true""")
        assertFalse(vm.uiState.value.newConversationNeedsConfirm)

        emitFrame(
            """{"t":"events","epoch":"e1","seq":2,"events":[{"seq":2,"type":"state",""" +
                """"patch":{"status":"running"}}]}""",
        )
        assertTrue(vm.uiState.first { it.state.status == ChatStatus.RUNNING }.newConversationNeedsConfirm)

        emitFrame(
            """{"t":"events","epoch":"e1","seq":3,"events":[{"seq":3,"type":"state",""" +
                """"patch":{"status":"ready","queue":[{"id":"q:1","text":"next","images":0}]}}]}""",
        )
        assertTrue(vm.uiState.first { it.state.status == ChatStatus.READY }.newConversationNeedsConfirm)
    }

    @Test
    fun anAgentWithoutNewConversation_orOneThatIsStarting_getsNoCommand_andTheUserIsTold() = runTest {
        val vm = open(""""status":"ready"""")
        val refused = "Could not start a new conversation now. Try again."

        vm.messages.test {
            assertFalse(vm.uiState.value.offersNewConversation)
            vm.newConversation()
            assertTrue(newConversations().isEmpty())
            assertEquals(refused, awaitItem())

            emitFrame(
                """{"t":"events","epoch":"e1","seq":2,"events":[{"seq":2,"type":"state",""" +
                    """"patch":{"status":"starting","new_conversation":true}}]}""",
            )
            val starting = vm.uiState.first { it.state.status == ChatStatus.STARTING }

            assertTrue(starting.offersNewConversation)
            vm.newConversation()
            assertTrue(newConversations().isEmpty())
            assertEquals(refused, awaitItem())
        }
    }
}
