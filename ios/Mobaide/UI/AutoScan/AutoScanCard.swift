import SwiftUI

/// Status card for one autoscan instance (Android `AutoScanCard`), embedded in
/// the sessions list. Tapping opens the detail screen; the stop button ends the
/// scan.
struct AutoScanCard: View {
    let scan: AutoScanInstanceDto
    let onTap: () -> Void
    let onStop: () -> Void

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    Image(systemName: "dot.radiowaves.left.and.right")
                        .foregroundStyle(AutoScanDisplay.statusColor(scan.status))
                    Text(scan.project_name)
                        .font(.body)
                        .fontWeight(.medium)
                    Spacer()
                    AutoScanStatusChip(status: scan.status)
                    Button(action: onStop) {
                        Image(systemName: "stop.circle")
                            .foregroundStyle(.red)
                    }
                    .buttonStyle(.borderless)
                }
                HStack {
                    Text("Processed: \(scan.processed_count)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(AutoScanDisplay.relativeTime(scan.last_activity_at))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                CountdownView(nextScanAt: scan.next_scan_at)
            }
            .padding(.vertical, 4)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct AutoScanStatusChip: View {
    let status: String

    var body: some View {
        Text(AutoScanDisplay.statusLabel(status))
            .font(.caption2)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Capsule().fill(AutoScanDisplay.statusColor(status).opacity(0.2)))
            .foregroundStyle(AutoScanDisplay.statusColor(status))
    }
}

/// Status → label / color helpers shared by the card and detail screen
/// (Android `AutoScanCard.statusColor` / `StatusChip` / `phaseColor`).
enum AutoScanDisplay {
    static func statusColor(_ status: String) -> Color {
        switch status {
        case "scanning", "running", "connected": return MobaideColors.stateRunning
        case "idle", "starting", "connecting", "replaying": return MobaideColors.stateStarting
        case "error", "failed", "disconnected": return MobaideColors.stateCrashed
        case "stopped": return MobaideColors.stateStopped
        default: return MobaideColors.stateStopped
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

    static func phaseColor(_ status: String) -> Color {
        switch status {
        case "active": return MobaideColors.stateStarting
        case "ok": return MobaideColors.stateRunning
        case "failed": return MobaideColors.stateCrashed
        case "skipped": return MobaideColors.stateStopped
        default: return MobaideColors.stateStopped
        }
    }

    static func issueColor(_ status: String) -> Color {
        switch status {
        case "started": return MobaideColors.stateStarting
        case "ok": return MobaideColors.stateRunning
        case "failed": return MobaideColors.stateCrashed
        default: return MobaideColors.stateStopped
        }
    }
}
