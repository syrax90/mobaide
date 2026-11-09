import Foundation

/// The client copy of one chat session: a pure reducer over agent frames.
///
/// Sync rules: a snapshot replaces everything. An `events` frame from another
/// epoch asks for a fresh snapshot. Inside the frame, an event at or below the
/// applied `seq` is a duplicate (another client's sync reply), the next `seq`
/// applies, and anything further is a gap: the rest of the frame is dropped and
/// a resume sync from the applied `seq` is requested.
struct ChatTimeline: Sendable {
    enum SyncRequest: Equatable, Sendable {
        case fresh
        case resume(epoch: String, afterSeq: Int)

        var epoch: String? {
            if case .resume(let epoch, _) = self { return epoch }
            return nil
        }

        var afterSeq: Int? {
            if case .resume(_, let seq) = self { return seq }
            return nil
        }
    }

    struct Change: OptionSet, Sendable {
        let rawValue: Int
        static let items = Change(rawValue: 1)
        static let state = Change(rawValue: 2)
    }

    static let defaultMaxItems = 1500

    let maxItems: Int
    private(set) var epoch: String?
    private(set) var seq = 0
    private(set) var hasSnapshot = false
    private(set) var state = ChatState()
    private(set) var items: [ChatItem] = []
    /// Older items exist that this copy does not hold.
    private(set) var truncated = false
    private var index: [String: Int] = [:]

    init(maxItems: Int = ChatTimeline.defaultMaxItems) {
        self.maxItems = maxItems
    }

    /// What to send on a (re)opened socket.
    var syncRequest: SyncRequest {
        guard hasSnapshot, let epoch else { return .fresh }
        return .resume(epoch: epoch, afterSeq: seq)
    }

    /// Applies `frame`; returns what changed and a sync to send, if any.
    mutating func apply(_ frame: ChatServerFrame) -> (changes: Change, sync: SyncRequest?) {
        switch frame {
        case .snapshot(let snapshot):
            applySnapshot(snapshot)
            return ([.items, .state], nil)
        case .events(let events):
            return applyEvents(events)
        case .result:
            return ([], nil)
        }
    }

    private mutating func applySnapshot(_ snapshot: ChatSnapshotFrame) {
        epoch = snapshot.epoch
        seq = snapshot.seq
        hasSnapshot = true
        state = snapshot.state
        truncated = snapshot.truncated
        items = []
        index = [:]
        items.reserveCapacity(snapshot.items.count)
        for item in snapshot.items {
            upsert(item)
        }
        enforceCap()
    }

    private mutating func applyEvents(_ frame: ChatEventsFrame) -> (changes: Change, sync: SyncRequest?) {
        guard hasSnapshot, frame.epoch == epoch else { return ([], .fresh) }
        defer { enforceCap() }
        var changes: Change = []
        for event in frame.events {
            if event.seq <= seq { continue }
            guard event.seq == seq + 1 else {
                return (changes, .resume(epoch: frame.epoch, afterSeq: seq))
            }
            changes.formUnion(apply(event))
            seq = event.seq
        }
        // The frame's own `seq` is the agent's latest; still being behind it
        // after every event means some were never delivered.
        if frame.seq > seq {
            return (changes, .resume(epoch: frame.epoch, afterSeq: seq))
        }
        return (changes, nil)
    }

    private mutating func apply(_ event: ChatEvent) -> Change {
        switch event {
        case .item(_, let item):
            guard let item else { return [] }
            upsert(item)
            return .items
        case .append(_, let id, let text):
            guard let position = index[id], !text.isEmpty else { return [] }
            items[position].appendText(text)
            return .items
        case .state(_, let patch):
            state.apply(patch)
            return .state
        case .other:
            return []
        }
    }

    private mutating func upsert(_ item: ChatItem) {
        if let position = index[item.id] {
            items[position] = item
        } else {
            index[item.id] = items.count
            items.append(item)
        }
    }

    private mutating func enforceCap() {
        guard items.count > maxItems else { return }
        items.removeFirst(items.count - maxItems)
        truncated = true
        index = Dictionary(uniqueKeysWithValues: items.enumerated().map { ($1.id, $0) })
    }
}
