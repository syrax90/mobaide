package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.ChatStoredSession
import com.mobaide.app.data.network.dto.ChatStoredSessionsResponse
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Rule
import org.junit.Test

/**
 * Delete in "Continue a session": offered only on rows nothing has open when
 * the agent can delete, confirmed before the request, and on success the row
 * leaves the loaded list without a reload.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatResumeViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val closed = ChatStoredSession(session_id = "a", cwd = "/p", title = "Refactor the login flow")
    private val other = ChatStoredSession(session_id = "b", cwd = "/p", title = "Fix flaky upload test")
    private val openInMobaide = ChatStoredSession(session_id = "c", cwd = "/p", open_session_id = "mob-7")
    private val openElsewhere = ChatStoredSession(session_id = "d", cwd = "/p", open_elsewhere = true)

    private fun loadedViewModel(
        repo: SessionRepository,
        rows: List<ChatStoredSession>,
        canDelete: Boolean,
        nextCursor: String? = null,
    ): ChatResumeViewModel {
        coEvery { repo.listChatSessions("claude", "/p", null) } returns
            ChatStoredSessionsResponse(sessions = rows, next_cursor = nextCursor, can_delete = canDelete)
        return ChatResumeViewModel(repo).also { it.open("claude", "/p", live = false) }
    }

    private fun ChatResumeViewModel.rows(): List<String> =
        (uiState.value.list as ChatResumeListState.Loaded).rows.map { it.session_id }

    @Test
    fun askDelete_isRefusedForOpenRowsAndForAnAgentThatCannotDelete() = runTest {
        val repo = mockk<SessionRepository>(relaxed = true)
        val vm = loadedViewModel(repo, listOf(closed, openInMobaide, openElsewhere), canDelete = true)
        advanceUntilIdle()

        vm.askDelete(openInMobaide)
        vm.askDelete(openElsewhere)
        assertNull(vm.uiState.value.delete)

        val older = loadedViewModel(mockk(relaxed = true), listOf(closed), canDelete = false)
        advanceUntilIdle()
        older.askDelete(closed)
        assertNull(older.uiState.value.delete)

        vm.askDelete(closed)
        assertEquals(ChatResumeDeleteState(closed), vm.uiState.value.delete)
        coVerify(exactly = 0) { repo.deleteChatSession(any(), any()) }
    }

    @Test
    fun confirmDelete_removesTheRowInPlace() = runTest {
        val repo = mockk<SessionRepository>(relaxed = true)
        val vm = loadedViewModel(repo, listOf(closed, other), canDelete = true, nextCursor = "offset:50")
        advanceUntilIdle()

        vm.askDelete(closed)
        vm.confirmDelete()
        advanceUntilIdle()

        assertEquals(listOf("b"), vm.rows())
        assertNull(vm.uiState.value.delete)
        coVerify(exactly = 1) { repo.deleteChatSession("claude", "a") }
        coVerify(exactly = 1) { repo.listChatSessions(any(), any(), any()) }
    }

    @Test
    fun confirmDelete_ofTheLastRowOfTheLastPage_leavesAnEmptyList() = runTest {
        val repo = mockk<SessionRepository>(relaxed = true)
        val vm = loadedViewModel(repo, listOf(closed), canDelete = true)
        advanceUntilIdle()

        vm.askDelete(closed)
        vm.confirmDelete()
        advanceUntilIdle()

        assertEquals(ChatResumeListState.Empty, vm.uiState.value.list)
    }

    @Test
    fun confirmDelete_failure_keepsTheRowAndShowsTheAgentDetail() = runTest {
        val repo = mockk<SessionRepository>(relaxed = true)
        coEvery { repo.deleteChatSession("claude", "a") } throws ApiException(
            409,
            """{"detail":"This session is open in a Mobaide chat. Close the chat first."}""",
        )
        val vm = loadedViewModel(repo, listOf(closed, other), canDelete = true)
        advanceUntilIdle()

        vm.askDelete(closed)
        vm.confirmDelete()
        advanceUntilIdle()

        assertEquals(listOf("a", "b"), vm.rows())
        assertEquals(
            ChatResumeDeleteState(closed, deleting = false, error = "This session is open in a Mobaide chat. Close the chat first."),
            vm.uiState.value.delete,
        )
    }
}
