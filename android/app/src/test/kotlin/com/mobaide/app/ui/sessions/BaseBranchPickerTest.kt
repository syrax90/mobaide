package com.mobaide.app.ui.sessions

import com.mobaide.app.data.network.dto.WorktreeBaseRef
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.Instant

class BaseBranchPickerTest {

    private val refs = listOf(
        WorktreeBaseRef("origin/main", WorktreeBaseRef.KIND_REMOTE),
        WorktreeBaseRef("origin/Feature/Login", WorktreeBaseRef.KIND_REMOTE),
        WorktreeBaseRef("wip", WorktreeBaseRef.KIND_LOCAL),
    )

    @Test
    fun filter_isCaseInsensitiveSubstring_andBlankKeepsAll() {
        assertEquals(refs, filterBaseRefs(refs, "  "))
        assertEquals(listOf("origin/Feature/Login"), filterBaseRefs(refs, "feature").map { it.ref })
        assertEquals(listOf("origin/main", "origin/Feature/Login"), filterBaseRefs(refs, " ORIGIN ").map { it.ref })
        assertEquals(emptyList<WorktreeBaseRef>(), filterBaseRefs(refs, "nope"))
    }

    @Test
    fun relativeCommitTime_readsTheOffset() {
        val now = Instant.parse("2026-11-09T12:00:00Z")
        assertEquals("just now", relativeCommitTime("2026-11-09T11:59:30Z", now))
        assertEquals("5m ago", relativeCommitTime("2026-11-09T13:55:00+02:00", now))
        assertEquals("3h ago", relativeCommitTime("2026-11-09T09:00:00+00:00", now))
        assertEquals("2d ago", relativeCommitTime("2026-11-07T10:00:00+00:00", now))
        assertEquals("just now", relativeCommitTime("2026-11-10T00:00:00Z", now))
        assertNull(relativeCommitTime(null, now))
        assertNull(relativeCommitTime("yesterday", now))
    }
}
