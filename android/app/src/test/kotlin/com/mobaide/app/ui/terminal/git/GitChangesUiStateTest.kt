package com.mobaide.app.ui.terminal.git

import com.mobaide.app.data.network.dto.GitStatusCounts
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import org.junit.Assert.assertEquals
import org.junit.Test

/** The pure copy half of the Changes screen ([truncatedNotice]). */
class GitChangesUiStateTest {

    private fun untracked(path: String): GitStatusEntry = GitStatusEntry(
        group = GitStatusEntry.GROUP_UNTRACKED, path = path,
        code = "?", index_status = "?", worktree_status = "?",
    )

    private fun truncated(
        entries: List<GitStatusEntry>,
        counts: GitStatusCounts?,
    ): GitStatusResponse = GitStatusResponse(
        session_id = "s1",
        etag = "e1",
        entries = entries,
        counts = counts,
        truncated = true,
    )

    @Test
    fun truncatedNotice_showsWhatArrived_againstALowerBoundTotal() {
        val status = truncated(
            entries = listOf(untracked("build/a.o"), untracked("build/b.o")),
            counts = GitStatusCounts(untracked = 6001, total = 6001),
        )

        assertEquals("Showing the first 2 of at least 6001 entries.", truncatedNotice(status))
    }

    @Test
    fun truncatedNotice_withoutAHigherTotal_onlySaysTheListWasCut() {
        // The byte cap hit before the entry cap: the count is a lower bound no
        // higher than the list itself, so no total is claimed.
        val byteCapped = truncated(
            entries = listOf(untracked("build/a.o"), untracked("build/b.o")),
            counts = GitStatusCounts(untracked = 2, total = 2),
        )

        assertEquals("List cut short after 2 entries.", truncatedNotice(byteCapped))
        assertEquals("List cut short after 0 entries.", truncatedNotice(truncated(emptyList(), counts = null)))
    }
}
