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
import kotlinx.coroutines.test.runCurrent
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
 * Fork in [ChatViewModel]: a double tap sends one `fork`, the new session's
 * row reaches the session list before the screen opens it and becomes the
 * last-used session, and a fork the agent does not offer is never sent.
 * The socket is a fake that records `'c'` commands and pushes `'a'` frames.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatViewModelForkTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val socketEvents = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val sent = mutableListOf<JsonObject>()
    private val noted = mutableListOf<SessionResponse>()
    private val lastUsed = mutableListOf<String?>()

    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns socketEvents
        every { sendBinary(any()) } answers {
            sent += Json.parseToJsonElement(firstArg<ByteString>().substring(1).utf8()).jsonObject
            true
        }
    }

    private val session = SessionResponse(session_id = "chat-1", state = "running", created_at = "2026-11-27T00:00:00Z")

    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("chat-1") } returns connection
        every { observeSessionList() } returns MutableStateFlow(listOf(session))
        every { observeSessionsSynced() } returns MutableStateFlow(true)
        coEvery { listSessions() } returns listOf(session)
        every { noteCreatedSession(any()) } answers { noted += firstArg<SessionResponse>() }
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
            preferencesStore = mockk {
                every { chatFocusView } returns flowOf(true)
                coEvery { setLastUsedSessionId(any()) } answers { lastUsed += firstArg<String?>() }
            },
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

    private fun forks(): List<JsonObject> = sent.filter { it["op"]?.jsonPrimitive?.content == "fork" }

    @Test
    fun aDoubleTap_sendsOneFork_andTheNewSessionIsInTheListWhenTheScreenOpensIt() = runTest {
        val vm = open(""""status":"ready","fork":{"chat":true,"at_answer":true}""")

        vm.fork("m:4")
        vm.fork("m:4")

        val fork = forks().single()
        assertEquals("m:4", fork["item_id"]?.jsonPrimitive?.content)
        assertTrue(vm.uiState.value.forking)
        assertFalse(vm.uiState.value.canFork)

        vm.openSession.test {
            val id = fork["id"]!!.jsonPrimitive.content
            emitFrame(
                """{"t":"result","id":"$id","ok":true,"data":{"session_id":"chat-2",""" +
                    """"session":{"session_id":"chat-2","state":"running","created_at":"2026-11-27T00:00:01Z","ui":"chat"}}}""",
            )

            assertEquals("chat-2", awaitItem())
            assertEquals(listOf("chat-2"), noted.map { it.session_id })
        }
        assertFalse(vm.uiState.first { !it.forking }.forking)
        runCurrent()
        assertEquals(listOf("chat-2"), lastUsed)
    }

    @Test
    fun anAgentThatForksOnlyTheWholeChat_getsNoForkAtAnAnswer_andAFailureShowsTheAgentsError() = runTest {
        val vm = open(""""status":"ready","fork":{"chat":true,"at_answer":false}""")
        val busy = "Wait for the turn to end, or stop it, then fork the chat."

        vm.messages.test {
            assertFalse(vm.uiState.value.offersForkAtAnswer)
            vm.fork("m:4")
            assertTrue(forks().isEmpty())
            assertEquals("Could not fork the chat now. Try again.", awaitItem())

            vm.fork(null)
            val id = forks().single()["id"]!!.jsonPrimitive.content
            emitFrame("""{"t":"result","id":"$id","ok":false,"error":"$busy"}""")

            assertEquals(busy, awaitItem())
        }
        runCurrent()
        assertTrue(noted.isEmpty())
        assertTrue(lastUsed.isEmpty())
    }
}
