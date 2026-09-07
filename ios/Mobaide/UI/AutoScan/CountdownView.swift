import SwiftUI

/// A self-contained 1 Hz countdown to an ISO-8601 `next_scan_at` (Android
/// `CountdownText`). Isolated in its own view with its own `TimelineView` so the
/// per-second tick never re-renders the surrounding header or log list.
struct CountdownView: View {
    let nextScanAt: String?
    var prefix = "Next scan in "

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(label(now: context.date))
                .font(.caption)
                .monospacedDigit()
                .foregroundStyle(colors.textMuted)
        }
    }

    private func label(now: Date) -> String {
        guard let target = Self.parse(nextScanAt) else { return "—" }
        let secondsLeft = Int(target.timeIntervalSince(now).rounded())
        if secondsLeft <= 0 { return "\(prefix)now" }
        let minutes = secondsLeft / 60
        let seconds = secondsLeft % 60
        return minutes > 0 ? "\(prefix)\(minutes)m \(seconds)s" : "\(prefix)\(seconds)s"
    }

    static func parse(_ iso: String?) -> Date? {
        guard let iso, !iso.isEmpty else { return nil }
        return fractionalFormatter.date(from: iso) ?? plainFormatter.date(from: iso)
    }

    private static let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let plainFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}
