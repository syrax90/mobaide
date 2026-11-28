import Foundation

/// `GET /api/fs/dirs`: subdirectory names of one agent-host directory. `path`
/// is the listed directory after the agent's `~` expansion; `truncated` means
/// the agent stopped early, so the list must not be filtered locally as complete.
struct FsDirsResponse: Codable, Equatable, Sendable {
    let path: String
    let dirs: [String]
    let truncated: Bool
}

/// One file or folder of a session's folder: relative, `/` separators,
/// folders without a trailing one.
struct SessionFileEntry: Codable, Equatable, Hashable, Sendable {
    let path: String
    let dir: Bool
}

/// `GET /api/sessions/{id}/files`: the agent's best matches for an `@`
/// mention, best first. `truncated` means a cap stopped the listing behind them.
struct SessionFilesResponse: Codable, Equatable, Sendable {
    let entries: [SessionFileEntry]
    let truncated: Bool
}
