import Foundation

/// `GET /api/fs/dirs`: subdirectory names of one agent-host directory. `path`
/// is the listed directory after the agent's `~` expansion; `truncated` means
/// the agent stopped early, so the list must not be filtered locally as complete.
struct FsDirsResponse: Codable, Equatable, Sendable {
    let path: String
    let dirs: [String]
    let truncated: Bool
}
