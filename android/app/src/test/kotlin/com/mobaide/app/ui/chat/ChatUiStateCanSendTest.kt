package com.mobaide.app.ui.chat

import android.graphics.Bitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.mobaide.app.data.chat.ChatImagePayload
import com.mobaide.app.data.chat.ChatState
import com.mobaide.app.data.chat.ChatStatus
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * [ChatUiState.canSend]: the Send rule of the full-screen chat composer. A draft
 * goes out when it has text that is not only whitespace, or picked images, and
 * the agent can take it.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ChatUiStateCanSendTest {

    private fun ui(
        status: String = ChatStatus.READY,
        live: Boolean = true,
        images: List<ChatComposerImage> = emptyList(),
        imagesLoading: Boolean = false,
    ) = ChatUiState(
        loaded = true,
        state = ChatState(status = status),
        live = live,
        images = images,
        imagesLoading = imagesLoading,
    )

    private fun image() = ChatComposerImage(
        id = 1L,
        payload = ChatImagePayload("image/jpeg", "aGk="),
        thumbnail = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888).asImageBitmap(),
    )

    @Test
    fun aDraftOfOnlyWhitespace_cannotBeSent_butAnyTextCan() {
        assertFalse(ui().canSend(""))
        assertFalse(ui().canSend("  \n\t "))
        assertTrue(ui().canSend(" fix it \n"))
    }

    @Test
    fun pickedImages_canBeSentWithABlankDraft() {
        assertTrue(ui(images = listOf(image())).canSend("   "))
    }

    @Test
    fun nothingCanBeSent_whileImagesAreRead() {
        assertFalse(ui(images = listOf(image()), imagesLoading = true).canSend("fix it"))
    }

    @Test
    fun aDraftCanBeSentWhileATurnRuns_butNotWhenTheAgentCannotAct() {
        assertTrue(ui(status = ChatStatus.RUNNING).canSend("and the tests"))
        assertFalse(ui(live = false).canSend("fix it"))
        assertFalse(ui(status = ChatStatus.ERROR).canSend("fix it"))
    }
}
