package com.claudemobile.app.ui.terminal

import com.termux.terminal.TerminalSession
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed class PromptState {
    data object None : PromptState()

    data class Checkbox(
        val title: String,
        val items: List<CheckboxItem>,
    ) : PromptState()

    data class TextInput(
        val question: String,
    ) : PromptState()
}

data class CheckboxItem(
    val label: String,
    val selected: Boolean,
    val isCursor: Boolean,
)

/**
 * Reads the visible portion of the Termux [com.termux.terminal.TerminalBuffer] when terminal
 * output arrives and classifies whether Claude Code is waiting for user input. Detection is
 * debounced (100 ms after last screen update) and callback-driven — no polling.
 */
class PromptDetector {

    private val _promptState = MutableStateFlow<PromptState>(PromptState.None)
    val promptState: StateFlow<PromptState> = _promptState.asStateFlow()

    private var debounceJob: Job? = null

    /** Called from [TerminalSessionCallback.onTextChanged] on every screen update. */
    fun onScreenChanged(scope: CoroutineScope, session: TerminalSession) {
        debounceJob?.cancel()
        debounceJob = scope.launch {
            delay(DEBOUNCE_MS)
            _promptState.value = detectPrompt(session)
        }
    }

    /** Manually dismiss the current prompt (e.g. user closed the bottom sheet). */
    fun dismiss() {
        _promptState.value = PromptState.None
    }

    private fun detectPrompt(session: TerminalSession): PromptState {
        val emulator = session.emulator ?: return PromptState.None
        val screen = emulator.screen
        val rows = emulator.mRows
        val cols = emulator.mColumns

        // Read the bottom portion of the visible screen where prompts appear.
        val linesToCheck = minOf(rows, MAX_SCAN_ROWS)
        val startRow = rows - linesToCheck
        val text = screen.getSelectedText(0, startRow, cols - 1, rows - 1, false)
        val lines = text.split('\n').map { it.trimEnd() }

        return detectCheckbox(lines)
            ?: detectTextInput(lines)
            ?: PromptState.None
    }

    private fun detectCheckbox(lines: List<String>): PromptState.Checkbox? {
        val items = mutableListOf<CheckboxItem>()
        var titleLine: String? = null

        for ((index, line) in lines.withIndex()) {
            val checkboxMatch = CHECKBOX_PATTERN.find(line)
            if (checkboxMatch != null) {
                if (items.isEmpty() && index > 0) {
                    // First match — look upward for the title/question line.
                    titleLine = lines.subList(0, index)
                        .lastOrNull { it.isNotBlank() && !CHECKBOX_PATTERN.containsMatchIn(it) }
                }
                val marker = checkboxMatch.groupValues[1]
                val label = checkboxMatch.groupValues[2].trim()
                val selected = marker in setOf("x", "X", "\u25C9") // [x], [X], ◉
                val isCursor = line.trimStart().startsWith("\u276F") // ❯
                items.add(CheckboxItem(label, selected, isCursor))
                continue
            }
            val radioMatch = RADIO_PATTERN.find(line)
            if (radioMatch != null) {
                if (items.isEmpty() && index > 0) {
                    titleLine = lines.subList(0, index)
                        .lastOrNull { it.isNotBlank() && !RADIO_PATTERN.containsMatchIn(it) }
                }
                val marker = radioMatch.groupValues[1]
                val label = radioMatch.groupValues[2].trim()
                val selected = marker == "\u25C9" // ◉
                val isCursor = line.trimStart().startsWith("\u276F") // ❯
                items.add(CheckboxItem(label, selected, isCursor))
            }
        }

        if (items.size < 2) return null // Need at least 2 items to be a real list
        return PromptState.Checkbox(
            title = titleLine?.trim() ?: "",
            items = items,
        )
    }

    private fun detectTextInput(lines: List<String>): PromptState.TextInput? {
        // Scan from bottom up — the question is usually the last non-blank line.
        for (line in lines.asReversed()) {
            if (line.isBlank()) continue
            for (pattern in TEXT_INPUT_PATTERNS) {
                if (pattern.containsMatchIn(line)) {
                    return PromptState.TextInput(question = line.trim())
                }
            }
            break
        }
        return null
    }

    companion object {
        private const val DEBOUNCE_MS = 100L
        private const val MAX_SCAN_ROWS = 15

        // Checkbox / multi-select patterns: matches [ ], [x], [X]
        private val CHECKBOX_PATTERN = Regex("""\[([xX ])][ ]+(.+)""")

        // Radio button patterns: matches ◯ / ◉ (optionally preceded by ❯)
        private val RADIO_PATTERN = Regex("""[\u276F ]?\s*([\u25CB\u25C9])\s+(.+)""")

        // Text input patterns (last line of screen)
        private val TEXT_INPUT_PATTERNS = listOf(
            Regex(""".+\?\s*$"""),            // Ends with ?
            Regex("""(?i)enter .+:\s*$"""),    // "Enter something:"
            Regex("""(?i)provide .+:\s*$"""),  // "Provide something:"
            Regex("""(?i)type .+:\s*$"""),     // "Type something:"
            Regex(""">\s*$"""),                // Bare prompt ">"
        )
    }
}
