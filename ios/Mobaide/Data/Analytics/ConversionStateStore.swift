import Foundation

/// Per-install state of the once-per-account `agent_paired` conversion: the
/// arming time and the outcome per account.
///
/// A JSON file under Application Support flagged excluded from backup, so
/// neither an iCloud or encrypted backup nor a device transfer carries an
/// older install's arming time onto this one — a restored stamp would let a
/// pairing made in between pass the window gate and count as a fresh install
/// conversion. Uninstalling removes it. Never swept on sign-out: a
/// sign-out/sign-in cycle must not log again.
struct ConversionStateStore: @unchecked Sendable {
    private struct State: Codable {
        var armedAt: Double?
        var outcomes: [String: String] = [:]
    }

    static let fileName = "agent_paired_conversion.json"

    private let fileURL: URL
    private let lock = NSLock()

    init(directory: URL? = nil) {
        let base = directory
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("Mobaide", isDirectory: true)
        fileURL = base.appendingPathComponent(Self.fileName)
    }

    func getOutcome(userId: String) -> String? {
        lock.withLock { load().outcomes[userId] }
    }

    func setOutcome(userId: String, outcome: String) {
        lock.withLock {
            var state = load()
            state.outcomes[userId] = outcome
            save(state)
        }
    }

    func getArmedAt() -> Date? {
        lock.withLock { load().armedAt.map { Date(timeIntervalSince1970: $0) } }
    }

    /// Set-if-absent: the first Firebase-capable launch fixes the moment
    /// before which a first pairing does not count for this install.
    func armIfUnset(now: Date) -> Date {
        lock.withLock {
            var state = load()
            if let existing = state.armedAt { return Date(timeIntervalSince1970: existing) }
            state.armedAt = now.timeIntervalSince1970
            save(state)
            return now
        }
    }

    private func load() -> State {
        guard let data = try? Data(contentsOf: fileURL),
              let state = try? JSONDecoder().decode(State.self, from: data)
        else { return State() }
        return state
    }

    private func save(_ state: State) {
        let directory = fileURL.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        guard let data = try? JSONEncoder().encode(state) else { return }
        try? data.write(to: fileURL, options: .atomic)
        var url = fileURL
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? url.setResourceValues(values)
    }
}
