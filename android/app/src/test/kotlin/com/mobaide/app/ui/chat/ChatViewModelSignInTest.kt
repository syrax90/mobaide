package com.mobaide.app.ui.chat

import app.cash.turbine.test
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.chat.ChatProtocol
import com.mobaide.app.data.chat.ChatRequest
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
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The sign-in card's commands in [ChatViewModel]: start, submit (trimmed, never
 * blank, never while the PC checks the last code) and cancel, one at a time per
 * card, with a refusal shown on the card. The socket is a fake that records
 * `'c'` commands and pushes `'a'` frames.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatViewModelSignInTest {

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

    private val session = SessionResponse(session_id = "chat-1", state = "running", created_at = "2026-11-11T00:00:00Z")

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

    /** A ready chat whose only request is a sign-in with these extra fields. */
    private suspend fun openWithSignIn(fields: String = ""): Pair<ChatViewModel, ChatRequest.SignIn> {
        val vm = createViewModel()
        socketEvents.emit(WsEvent.Connected)
        emitFrame(
            """{"t":"snapshot","epoch":"e1","seq":1,"items":[],"state":{"status":"ready","requests":[""" +
                """{"id":"sign-in","kind":"sign_in","tool_call_id":null,"title":"Sign in to Claude Code",""" +
                """"text":"","methods":[{"id":"claude_ai","name":"Claude subscription","description":""}]""" +
                fields + "}]}}",
        )
        val ui = vm.uiState.first { it.live && it.state.requests.isNotEmpty() }
        return vm to ui.state.requests.single() as ChatRequest.SignIn
    }

    private suspend fun waitingForCode(busy: Boolean = false) = openWithSignIn(
        ""","stage":"waiting","url":"https://example.com/sign-in",""" +
            """"input":{"label":"Code from the sign-in page","secret":false},"busy":$busy""",
    )

    private fun signInCommands(): List<JsonObject> = sent.filter { it["op"]?.jsonPrimitive?.content == "sign_in" }

    private fun withoutId(command: JsonObject): JsonObject = JsonObject(command - "id")

    private suspend fun answerLast(ok: Boolean, error: String? = null) {
        val id = signInCommands().last()["id"]!!.jsonPrimitive.content
        val errorField = error?.let { ""","error":"$it"""" }.orEmpty()
        emitFrame("""{"t":"result","id":"$id","ok":$ok$errorField}""")
    }

    @Test
    fun startSignIn_sendsTheMethod_andMarksTheCardBusyUntilTheResult() = runTest {
        val (vm, request) = openWithSignIn()

        vm.startSignIn(request, "claude_ai")
        vm.startSignIn(request, "claude_ai")
        vm.cancelSignIn(request)

        assertEquals(
            listOf(
                buildJsonObject {
                    put("op", "sign_in")
                    put("action", "start")
                    put("method", "claude_ai")
                },
            ),
            signInCommands().map(::withoutId),
        )
        assertEquals(ChatViewModel.signInStartKey("claude_ai"), vm.uiState.value.answering["sign-in"])

        answerLast(ok = true)

        vm.uiState.first { "sign-in" !in it.answering }
        vm.cancelSignIn(request)
        assertEquals(
            buildJsonObject {
                put("op", "sign_in")
                put("action", "cancel")
            },
            withoutId(signInCommands().last()),
        )
    }

    @Test
    fun submitSignIn_sendsTheTrimmedCode() = runTest {
        val (vm, request) = waitingForCode()

        vm.submitSignIn(request, "  ab12-cd34 \n")

        assertEquals(
            listOf(
                buildJsonObject {
                    put("op", "sign_in")
                    put("action", "submit")
                    put("value", "ab12-cd34")
                },
            ),
            signInCommands().map(::withoutId),
        )
        assertEquals(ChatViewModel.SIGN_IN_SUBMIT, vm.uiState.value.answering["sign-in"])
    }

    @Test
    fun submitSignIn_blankCode_sendsNothing() = runTest {
        val (vm, request) = waitingForCode()

        vm.submitSignIn(request, "   ")

        assertTrue(signInCommands().isEmpty())
        assertNull(vm.uiState.value.answering["sign-in"])
    }

    @Test
    fun submitSignIn_whileThePcChecksTheLastCode_sendsNothing() = runTest {
        val (vm, request) = waitingForCode(busy = true)

        vm.submitSignIn(request, "ab12-cd34")

        assertTrue(signInCommands().isEmpty())
    }

    @Test
    fun aRefusedCommand_showsTheAgentsErrorOnTheCard() = runTest {
        val (vm, request) = waitingForCode()

        vm.submitSignIn(request, "ab12")
        answerLast(ok = false, error = "The sign-in is not running")

        val ui = vm.uiState.first { "sign-in" !in it.answering }
        assertEquals("The sign-in is not running", ui.requestErrors["sign-in"])
    }

    @Test
    fun startSignIn_whenTheSocketTakesNoFrame_saysNotConnected_andLeavesTheCardEnabled() = runTest {
        val (vm, request) = openWithSignIn()
        socketAccepts = false

        vm.messages.test {
            vm.startSignIn(request, "claude_ai")

            assertEquals("Not connected. Try again.", awaitItem())
        }
        assertTrue(vm.uiState.value.answering.isEmpty())
    }

    @Test
    fun signInRequest_keepsOnlyAnHttpsSignInPage() = runTest {
        val (_, request) = openWithSignIn(""","stage":"waiting","url":"http://example.com/sign-in"""")

        assertEquals("waiting", request.stage)
        assertNull(request.url)
    }
}
