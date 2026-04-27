package com.mobaide.app.ui.terminal

/**
 * Translates semantic UI actions (allow, deny, toggle checkbox, submit) into the byte sequences
 * that Claude Code's Ink TUI expects. All sequences end up being sent through
 * [TerminalProtocol.encodeInput] and over the WebSocket.
 */
object InkSequenceBuilder {

    /** Free-text input: text + CR. */
    fun textInput(text: String): ByteArray =
        (text + "\r").toByteArray(Charsets.UTF_8)

    /** Checkbox / radio: Space toggles the currently-highlighted item. */
    fun checkboxToggle(): ByteArray = " ".toByteArray(Charsets.UTF_8)

    /** Checkbox / radio: Enter submits the selection. */
    fun checkboxSubmit(): ByteArray = byteArrayOf(0x0d)

    /** Arrow Up — move cursor up in a list. */
    fun arrowUp(): ByteArray = byteArrayOf(0x1b, '['.code.toByte(), 'A'.code.toByte())

    /** Arrow Down — move cursor down in a list. */
    fun arrowDown(): ByteArray = byteArrayOf(0x1b, '['.code.toByte(), 'B'.code.toByte())
}
