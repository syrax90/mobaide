import SwiftUI

/// Status card for one autoscan instance (Android `AutoScanCard`), embedded in
/// the sessions list. Tapping opens the detail screen; the stop button ends the
/// scan.
struct AutoScanCard: View {
    let scan: AutoScanInstanceDto
    let onTap: () -> Void
    let onStop: () -> Void

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    PixelStatusSquare(tone: AutoScanDisplay.statusTone(scan.status))
                    Text(scan.project_name)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(colors.text)
                    Spacer()
                    AutoScanStatusChip(status: scan.status)
                    Button(action: onStop) {
                        Image(systemName: "stop.fill")
                            .foregroundStyle(colors.danger)
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Stop scan")
                }
                HStack {
                    Text("Processed: \(scan.processed_count)")
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                    Spacer()
                    Text(AutoScanDisplay.relativeTime(scan.last_activity_at))
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                }
                CountdownView(nextScanAt: scan.next_scan_at)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct AutoScanStatusChip: View {
    let status: String

    var body: some View {
        PixelChip(AutoScanDisplay.statusLabel(status), .status(AutoScanDisplay.statusTone(status)))
    }
}

/// Status → label / tone helpers shared by the card and detail screen
/// (Android `AutoScanCard.statusColor` / `StatusChip` / `phaseColor`, resolved
/// through the scheme here so the tones follow the theme).
enum AutoScanDisplay {
    static func statusTone(_ status: String) -> PixelTone {
        switch status {
        case "scanning", "running", "connected": return .ok
        case "idle", "starting", "connecting", "replaying": return .accent
        case "error", "failed", "disconnected": return .danger
        default: return .muted
        }
    }

    static func statusLabel(_ status: String) -> String {
        switch status {
        case "scanning": return "Scanning"
        case "idle": return "Idle"
        case "stopped": return "Stopped"
        case "error": return "Error"
        case "starting": return "Starting"
        default: return status.isEmpty ? status : status.prefix(1).uppercased() + status.dropFirst()
        }
    }

    static func relativeTime(_ epochSeconds: Double) -> String {
        guard epochSeconds > 0 else { return "" }
        let elapsed = Int(Date().timeIntervalSince1970 - epochSeconds)
        switch elapsed {
        case ..<5: return "just now"
        case ..<60: return "\(elapsed)s ago"
        case ..<3600: return "\(elapsed / 60)m ago"
        case ..<86400: return "\(elapsed / 3600)h ago"
        default: return "\(elapsed / 86400)d ago"
        }
    }

    static func phaseTone(_ status: String) -> PixelTone {
        switch status {
        case "active": return .accent
        case "ok": return .ok
        case "failed": return .danger
        default: return .muted
        }
    }

    static func issueTone(_ status: String) -> PixelTone {
        switch status {
        case "started": return .accent
        case "ok": return .ok
        case "failed": return .danger
        default: return .muted
        }
    }
}
