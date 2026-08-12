import Foundation

/// When to stop consuming a session's PTY output. Mirrors the web client's
/// `terminal/terminalSuspend.ts` and Android's `TerminalSuspend`; the three
/// constants are the policy and must stay in step.
///
/// The session socket has no pause frame, so closing it is the only way to stop
/// the bytes — and undoing that is expensive, because the agent replays its
/// whole ring on every socket open (4 MiB by default, uncompressed) and the
/// client wipes the emulator before writing it.
///
/// So neither half of the obvious policy works alone. Time alone charges an
/// idle session a full replay to save the ~0 bytes it was not sending; bytes
/// alone fires on short absences, where staying attached was cheaper than the
/// replay it triggers. Suspension pays only when the session is *both*
/// unwatched for a while *and* actually spending — hence the conjunction, with
/// `firehoseBytes` as the one escape hatch so a session dumping tens of MiB
/// does not get to finish the grace window first.
enum TerminalSuspend {
    /// Bytes an unwatched session must have delivered before suspending it can
    /// pay for itself. One ring: below this the replay costs more than staying.
    static let recoupBytes: Int = 4 * 1024 * 1024

    /// Spend that ends the grace window early, for `yes`-grade output.
    static let firehoseBytes: Int = 32 * 1024 * 1024

    /// How long a session must have gone unwatched.
    static let graceSeconds: TimeInterval = 60

    static func shouldSuspend(unwatchedSeconds: TimeInterval, bytes: Int) -> Bool {
        if bytes < recoupBytes { return false }
        if bytes >= firehoseBytes { return true }
        return unwatchedSeconds >= graceSeconds
    }
}
