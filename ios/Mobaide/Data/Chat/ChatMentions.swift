import Foundation

/// Lists the agent's best matches for an `@` query in a session's folder.
typealias SessionFilesLister = @Sendable (_ sessionId: String, _ query: String) async throws -> [SessionFileEntry]

/// `@` file mentions in the chat composer: which token the cursor is in, what
/// a picked suggestion turns it into, and which picked files the text still
/// names when it is sent. Port of web `chat/chatMentions.ts` and Android
/// `ChatMentions.kt`. Offsets count `Character`s, as the composer's selection does.
enum ChatMentions {
    /// The agent takes at most this many per prompt.
    static let maxMentions = 10
    static let suggestionsLimit = 20
    static let debounce: Duration = .milliseconds(150)

    private static let trailingPunctuation: Set<Character> = [".", ",", ";", ":", "!", "?", ")", "]", "}", "'", "\""]
    /// How far back from the cursor a picked path's `@` is looked for (the agent's path cap).
    private static let pickedScanMax = 4096

    /// The `@` token around the cursor: `start..<end`; `query` is what is typed after the `@`.
    struct Token: Equatable {
        let start: Int
        let end: Int
        let query: String
    }

    /// The token the cursor is in when it starts with `@` at the start of the
    /// text or after whitespace, with no whitespace up to the cursor; nil otherwise.
    /// The `@` must be before the cursor: one just in front of it, or a cursor
    /// `composerText.didSet` reads before the selection moves, is not in the token.
    /// A `picked` path counts as one token even with spaces in it (`@my docs/`),
    /// together with what is typed right after it.
    static func token(in text: String, cursor: Int, picked: [String] = []) -> Token? {
        // Runs on every keystroke: skip the copy of a long draft with no `@`.
        guard text.utf8.contains(UInt8(ascii: "@")) else { return nil }
        let chars = Array(text)
        let at = min(max(cursor, 0), chars.count)
        return pickedToken(chars, at: at, picked: picked) ?? plainToken(chars, at: at)
    }

    /// The token of a picked path the cursor is in, found from the nearest `@` before the cursor.
    private static func pickedToken(_ chars: [Character], at: Int, picked: [String]) -> Token? {
        guard !picked.isEmpty else { return nil }
        let values = picked.map(Array.init)
        let floor = max(0, at - pickedScanMax)
        var p = at - 1
        while p >= floor, !chars[p].isNewline {
            if chars[p] == "@", p == 0 || chars[p - 1].isWhitespace {
                let longest = values.filter { value in
                    p + 1 + value.count <= chars.count && chars[(p + 1)..<(p + 1 + value.count)].elementsEqual(value)
                }.map(\.count).max()
                if let longest {
                    var end = p + 1 + longest
                    while end < chars.count, !chars[end].isWhitespace { end += 1 }
                    if at <= end { return Token(start: p, end: end, query: String(chars[(p + 1)..<at])) }
                }
            }
            p -= 1
        }
        return nil
    }

    private static func plainToken(_ chars: [Character], at: Int) -> Token? {
        var start = at
        while start > 0, !chars[start - 1].isWhitespace { start -= 1 }
        guard start < at, chars[start] == "@" else { return nil }
        var end = at
        while end < chars.count, !chars[end].isWhitespace { end += 1 }
        return Token(start: start, end: end, query: String(chars[(start + 1)..<at]))
    }

    /// What the composer holds for a picked entry: folders end in `/`.
    static func value(path: String, dir: Bool) -> String {
        dir ? path + "/" : path
    }

    /// `text` with `token` replaced by `@value`, and the cursor after it. A file
    /// is followed by a space (unless one is already there); a folder is not,
    /// so typing goes on inside it.
    static func insert(_ value: String, replacing token: Token, in text: String) -> (text: String, cursor: Int) {
        let chars = Array(text)
        let before = String(chars[0..<min(token.start, chars.count)])
        let after = String(chars[min(token.end, chars.count)...])
        let inserted = "@" + value
        if value.hasSuffix("/") {
            return (before + inserted + after, before.count + inserted.count)
        }
        let space = after.first?.isWhitespace == true ? "" : " "
        return (before + inserted + space + after, before.count + inserted.count + 1)
    }

    /// The `picked` mentions whose token is still in `text`, in pick order, at most `maxMentions`.
    static func inText(_ text: String, picked: [String]) -> [String] {
        var seen: Set<String> = []
        let chars = Array(text)
        return Array(
            picked.filter { seen.insert($0).inserted && names(chars, value: $0) }.prefix(maxMentions)
        )
    }

    private static func names(_ chars: [Character], value: String) -> Bool {
        let token = Array("@" + value)
        guard !token.isEmpty, chars.count >= token.count else { return false }
        for at in 0...(chars.count - token.count) where chars[at..<(at + token.count)].elementsEqual(token) {
            guard at == 0 || chars[at - 1].isWhitespace else { continue }
            let end = at + token.count
            if end == chars.count || chars[end].isWhitespace { return true }
            if trailingPunctuation.contains(chars[end]), end + 1 == chars.count || chars[end + 1].isWhitespace {
                return true
            }
        }
        return false
    }
}
