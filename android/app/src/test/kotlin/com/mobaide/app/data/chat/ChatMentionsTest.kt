package com.mobaide.app.data.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * [ChatMentions]: which `@` token the cursor is in, what a picked suggestion
 * turns it into, and which picked files a sent prompt carries. Same rules as
 * web `chatMentions.ts` and iOS `ChatMentions.swift`.
 */
class ChatMentionsTest {

    @Test
    fun tokenAt_findsTheAtTokenAroundTheCursor() {
        assertEquals(ChatMentions.Token(0, 3, "sr"), ChatMentions.tokenAt("@sr", 3))
        assertEquals(ChatMentions.Token(4, 11, "src/ap"), ChatMentions.tokenAt("see @src/ap now", 11))
        // Back inside a picked mention: the query is what lies before the cursor.
        assertEquals(ChatMentions.Token(0, 11, "src"), ChatMentions.tokenAt("@src/app.py", 4))
    }

    @Test
    fun tokenAt_findsNoneInAnEmailAnotherWordOrPlainText() {
        assertNull(ChatMentions.tokenAt("mail me@host.com", 16))
        assertNull(ChatMentions.tokenAt("a @b c", 6))
        assertNull(ChatMentions.tokenAt("plain", 3))
        assertNull(ChatMentions.tokenAt("", 0))
    }

    @Test
    fun tokenAt_findsNoneWithTheCursorJustInFrontOfTheAt() {
        assertNull(ChatMentions.tokenAt("@sr", 0))
        assertNull(ChatMentions.tokenAt("hi @", 3))
    }

    @Test
    fun tokenAt_keepsAPickedPathWithSpacesAsOneToken() {
        val inFolder = "see @my docs/re"
        val backInFile = "see @my docs/read me.md now"

        val fileToken = ChatMentions.tokenAt(backInFile, 7, listOf("my docs/read me.md"))

        assertNull(ChatMentions.tokenAt(inFolder, 15))
        assertEquals(ChatMentions.Token(4, 15, "my docs/re"), ChatMentions.tokenAt(inFolder, 15, listOf("my docs/")))
        assertEquals(ChatMentions.Token(4, 23, "my"), fileToken)
        assertEquals(
            "see @my docs/other.md now" to 22,
            ChatMentions.insert(backInFile, fileToken!!, "my docs/other.md"),
        )
    }

    @Test
    fun insert_endsAFileWithOneSpace_andLeavesTheCursorInsideAFolder() {
        assertEquals("@src/app.py " to 12, ChatMentions.insert("@sr", ChatMentions.Token(0, 3, "sr"), "src/app.py"))
        assertEquals(
            "fix @src/app.py now" to 16,
            ChatMentions.insert("fix @sr now", ChatMentions.Token(4, 7, "sr"), "src/app.py"),
        )

        val (text, cursor) = ChatMentions.insert("@sr", ChatMentions.Token(0, 3, "sr"), "src/")

        assertEquals("@src/" to 5, text to cursor)
        assertEquals("src/", ChatMentions.tokenAt(text, cursor)?.query)
    }

    @Test
    fun inText_sendsThePickedMentionsTheTextStillNames_inPickOrder_onceEach() {
        val picked = listOf("src/app.py", "gone.ts", "docs/", "src/app.py", "lib/x.ts", "lib/y.ts")
        val text = "Explain @src/app.py, then @docs/ (see lib/x.ts) x@lib/y.ts @src/app.py.bak"

        assertEquals(listOf("src/app.py", "docs/"), ChatMentions.inText(text, picked))
    }

    @Test
    fun inText_sendsOnlyTheLongestPickedPathATokenNames() {
        val picked = listOf("notes.txt", "notes.txt backup")

        assertEquals(listOf("notes.txt backup"), ChatMentions.inText("Read @notes.txt backup ", picked))
        assertEquals(picked, ChatMentions.inText("Read @notes.txt and @notes.txt backup", picked))
    }

    @Test
    fun inText_sendsAtMostMaxMentions() {
        val picked = (0 until ChatMentions.MAX_MENTIONS + 2).map { "f$it.ts" }

        assertEquals(
            picked.take(ChatMentions.MAX_MENTIONS),
            ChatMentions.inText(picked.joinToString(" ") { "@$it" }, picked),
        )
    }
}
