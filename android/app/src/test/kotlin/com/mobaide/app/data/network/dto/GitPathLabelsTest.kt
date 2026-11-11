package com.mobaide.app.data.network.dto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The row name and directory [gitPathFileName] / [gitPathDirectory] split a status path into. */
class GitPathLabelsTest {

    @Test
    fun aNestedFile_splitsOnTheLastSlash() {
        assertEquals("c.txt", gitPathFileName("a/b/c.txt"))
        assertEquals("a/b", gitPathDirectory("a/b/c.txt"))
    }

    @Test
    fun aTopLevelFile_hasNoDirectory() {
        assertEquals("README.md", gitPathFileName("README.md"))
        assertNull(gitPathDirectory("README.md"))
    }

    @Test
    fun aNestedRepositoryListedAsAFolder_keepsItsTrailingSlashOnTheName() {
        val path = "mobaide_worktree/claude-session-56a3f247/"

        assertEquals("claude-session-56a3f247/", gitPathFileName(path))
        assertEquals("mobaide_worktree", gitPathDirectory(path))
    }

}
