import SwiftUI

/// Android `KeepAwakeCopy` and web `AgentSettingsDialog.tsx` hold the same text.
enum KeepAwakeCopy {
    static let title = "Keep awake"

    static let noAgent = "Stops your computer from going to sleep while it is idle, so your sessions stay reachable. "
        + "Connect to an agent to set it up."

    static func label(_ agent: String) -> String { "Keep \(agent) awake" }

    static func hint(_ agent: String) -> String {
        "Stops \(agent) from going to sleep while it is idle, so your sessions stay reachable. "
            + "The screen can still turn off, except on some Windows laptops on battery, "
            + "where it stays on and does not lock by itself. Closing a laptop lid still puts it to sleep."
    }

    static func checking(_ agent: String) -> String { "Checking \(agent)…" }

    static func unreachable(_ agent: String) -> String { "Couldn’t reach \(agent). Is it online?" }

    static func outdated(_ agent: String) -> String {
        "\(agent) runs a Mobaide agent without this setting. It updates itself; check again after its next update."
    }

    static func saveFailed(_ agent: String) -> String { "Couldn’t save this setting on \(agent). Try again." }

    static func unsupported(_ agent: String, message: String?) -> String {
        message ?? "\(agent) can’t be kept awake."
    }

    static func failed(_ agent: String, message: String?) -> String {
        "\(agent) can’t stay awake: " + (message ?? "The computer did not say why.")
    }
}

/// Keep awake for the connected agent (Android `KeepAwakeSection`): a
/// checkbox row, and a line that says why the computer is not kept awake
/// when the agent could not do it, or the agent's note on how it keeps it
/// awake.
struct KeepAwakeSection: View {
    let model: KeepAwakeModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Section {
            content
        } header: {
            PixelSectionHeader(KeepAwakeCopy.title)
        }
        .pixelFormSection()
        .task { await model.load() }
        .task(id: model.availability) { await model.pollWhileUnreachable() }
    }

    @ViewBuilder
    private var content: some View {
        let name = model.agentName
        switch model.availability {
        case .noAgent:
            note(KeepAwakeCopy.noAgent)
        case .checking:
            note(KeepAwakeCopy.checking(name))
        case .unreachable:
            note(KeepAwakeCopy.unreachable(name))
        case .outdated:
            note(KeepAwakeCopy.outdated(name))
        case .available:
            if let status = model.status {
                row(status)
            } else {
                note(KeepAwakeCopy.checking(name))
            }
        }
    }

    private func row(_ status: KeepAwakeStatus) -> some View {
        let name = model.agentName
        let unsupported = status.state == KeepAwakeStatus.stateUnsupported
        let shown = rowNote(status)
        return VStack(alignment: .leading, spacing: 6) {
            Toggle(isOn: binding(status)) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(KeepAwakeCopy.label(name))
                    Text(KeepAwakeCopy.hint(name))
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                }
            }
            .disabled(model.saving != nil || (unsupported && !status.enabled))
            .accessibilityIdentifier("keep_awake_checkbox")
            if let shown {
                note(shown.text, error: shown.error)
            }
        }
        .padding(.vertical, 4)
        // Android's polite live region and web's role="status" read a new note aloud.
        .onChange(of: shown?.text) { _, text in
            if let text { AccessibilityNotification.Announcement(text).post() }
        }
    }

    private func rowNote(_ status: KeepAwakeStatus) -> (text: String, error: Bool)? {
        let name = model.agentName
        if model.saveFailed {
            return (KeepAwakeCopy.saveFailed(name), true)
        } else if status.state == KeepAwakeStatus.stateUnsupported {
            return (KeepAwakeCopy.unsupported(name, message: status.message), false)
        } else if model.saving == nil, status.enabled, status.state != KeepAwakeStatus.stateActive {
            return (KeepAwakeCopy.failed(name, message: status.message), true)
        } else if status.state == KeepAwakeStatus.stateActive, let message = status.message {
            return (message, false)
        }
        return nil
    }

    private func note(_ text: String, error: Bool = false) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(error ? colors.danger : colors.textMuted)
            .accessibilityIdentifier("keep_awake_note")
    }

    private func binding(_ status: KeepAwakeStatus) -> Binding<Bool> {
        Binding(
            get: { model.saving ?? status.enabled },
            set: { model.setKeepAwake($0) }
        )
    }
}
