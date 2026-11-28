package com.mobaide.app.ui.chat

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.chat.ChatMentions
import com.mobaide.app.data.chat.ChatProtocol
import com.mobaide.app.data.chat.ChatSessionCache
import com.mobaide.app.data.chat.ChatStatus
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionFileEntry
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import java.io.IOException
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
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * `@` file mentions in [ChatViewModel]: the agent is asked for matches only
 * when it lists files, after a pause in typing; a picked file goes out in the
 * prompt's `mentions`, and comes back with the draft when the prompt is refused.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatViewModelMentionsTest {

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

    private val session = SessionResponse(session_id = "chat-1", state = "running", created_at = "2026-11-27T00:00:00Z")

    private val files = listOf(SessionFileEntry("src/app.py", dir = false), SessionFileEntry("src", dir = true))

    /** The query of each listing the agent was asked for. */
    private val queries = mutableListOf<String>()
    private var listingFails = false

    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("chat-1") } returns connection
        every { observeSessionList() } returns MutableStateFlow(listOf(session))
        every { observeSessionsSynced() } returns MutableStateFlow(true)
        coEvery { listSessions() } returns listOf(session)
        coEvery { listSessionFiles("chat-1", any()) } coAnswers {
            queries += secondArg<String>()
            if (listingFails) throw IOException("agent offline")
            files
        }
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

    private suspend fun openReady(mentions: Boolean = true): ChatViewModel {
        val vm = createViewModel()
        socketEvents.emit(WsEvent.Connected)
        emitFrame(
            """{"t":"snapshot","epoch":"e1","seq":1,"items":[],""" +
                """"state":{"status":"ready","prompt_caps":{"image":false,"mentions":$mentions}}}""",
        )
        vm.uiState.first { it.canAct && it.state.status == ChatStatus.READY }
        return vm
    }

    private fun typed(text: String) = TextFieldValue(text, TextRange(text.length))

    private fun prompts(): List<JsonObject> = sent.filter { it["op"]?.jsonPrimitive?.content == "prompt" }

    private fun mentions(prompt: JsonObject): List<String> =
        prompt["mentions"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty()

    @Test
    fun typingAfterAt_listsAfterThePause_andAPickedFileGoesOutAsAMention() = runTest {
        val vm = openReady()

        vm.updateComposer(typed("explain @s"))
        advanceTimeBy(ChatMentions.DEBOUNCE_MS / 2)
        vm.updateComposer(typed("explain @sr"))
        advanceTimeBy(ChatMentions.DEBOUNCE_MS - 1)
        runCurrent()
        assertEquals(emptyList<String>(), queries)

        advanceTimeBy(1)
        runCurrent()
        assertEquals(listOf("sr"), queries)
        assertEquals(files, vm.mentionFiles.value)

        vm.pickMention(files[0])
        assertEquals(TextFieldValue("explain @src/app.py ", TextRange(20)), vm.composer.value)
        assertEquals(emptyList<SessionFileEntry>(), vm.mentionFiles.value)

        vm.sendPrompt()

        val prompt = prompts().single()
        assertEquals("explain @src/app.py", prompt["text"]!!.jsonPrimitive.content)
        assertEquals(listOf("src/app.py"), mentions(prompt))
    }

    @Test
    fun aRefusedPrompt_comesBackWithItsMentions_forTheNextSend() = runTest {
        val vm = openReady()
        vm.updateComposer(typed("read @sr"))
        vm.pickMention(files[0])
        vm.sendPrompt()
        val refused = prompts().single()

        emitFrame("""{"t":"result","id":"${refused["id"]!!.jsonPrimitive.content}","ok":false,"error":"Too many queued messages"}""")
        // The frame is decoded off the main dispatcher: wait for the draft to come back.
        vm.composer.first { it.text.isNotEmpty() }
        assertEquals("read @src/app.py", vm.composer.value.text)
        vm.sendPrompt()

        assertEquals(listOf("src/app.py"), mentions(prompts().last()))
        assertEquals(2, prompts().size)
    }

    @Test
    fun anAgentThatDoesNotListFiles_isNeverAsked() = runTest {
        val vm = openReady(mentions = false)

        vm.updateComposer(typed("explain @sr"))
        advanceTimeBy(ChatMentions.DEBOUNCE_MS * 2)
        runCurrent()

        assertTrue(queries.isEmpty())
        assertTrue(vm.mentionFiles.value.isEmpty())
    }

    @Test
    fun aListingThatFails_leavesNoSuggestions() = runTest {
        listingFails = true
        val vm = openReady()

        vm.updateComposer(typed("explain @sr"))
        advanceTimeBy(ChatMentions.DEBOUNCE_MS)
        runCurrent()

        assertEquals(listOf("sr"), queries)
        assertTrue(vm.mentionFiles.value.isEmpty())
    }
}
