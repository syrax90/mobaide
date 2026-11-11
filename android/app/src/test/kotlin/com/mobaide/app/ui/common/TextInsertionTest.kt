package com.mobaide.app.ui.common

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [insertAtCursor]: a template body (or a dictated transcript, [spaced]) goes
 * in at the composer's cursor, replacing a selection, with the rest of the
 * draft kept and the cursor right after the inserted text.
 */
class TextInsertionTest {

    private fun draft(text: String, selection: TextRange) = TextFieldValue(text, selection)

    @Test
    fun atTheCursor_keepsTheTextOnBothSides() {
        val result = draft("please  now", TextRange(7)).insertAtCursor("fix it")

        assertEquals(TextFieldValue("please fix it now", TextRange(13)), result)
    }

    @Test
    fun aSelectionIsReplaced_whateverItsDirection() {
        val forward = draft("run the old tests", TextRange(8, 11)).insertAtCursor("new")
        val backward = draft("run the old tests", TextRange(11, 8)).insertAtCursor("new")

        assertEquals(TextFieldValue("run the new tests", TextRange(11)), forward)
        assertEquals(forward, backward)
    }

    @Test
    fun unspaced_goesInAsIs_evenInsideAWord() {
        val result = draft("pleasenow", TextRange(6)).insertAtCursor("\n- step\n")

        assertEquals(TextFieldValue("please\n- step\nnow", TextRange(14)), result)
    }

    @Test
    fun spaced_addsASpaceOnlyWhereTheTextWouldRunIntoAWord() {
        val inside = draft("pleasenow", TextRange(6)).insertAtCursor("fix it", spaced = true)
        val afterASpace = draft("please now", TextRange(7)).insertAtCursor("fix it", spaced = true)

        assertEquals(TextFieldValue("please fix it now", TextRange(14)), inside)
        assertEquals(TextFieldValue("please fix it now", TextRange(14)), afterASpace)
    }
}
