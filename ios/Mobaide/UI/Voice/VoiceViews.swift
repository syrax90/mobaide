import SwiftUI
import UIKit

/// The composer's mic. Every state has its own symbol and label, so none
/// relies on colour.
struct VoiceMicButton: View {
    let voice: VoiceInputModel

    var body: some View {
        if let appearance {
            Button {
                voice.micTapped()
            } label: {
                Image(systemName: appearance.symbol)
            }
            .accessibilityLabel(appearance.label)
            .accessibilityIdentifier("voice_mic_button")
        }
    }

    private var appearance: (symbol: String, label: String)? {
        switch voice.micMode {
        case .hidden: return nil
        case .ready: return ("mic.fill", "Dictate")
        case .off: return ("mic", "Voice input is off. Set it up")
        case .preparing:
            return ("arrow.down.circle", voice.status.map(VoiceStatusView.preparingLabel) ?? "Preparing voice input")
        case .failed: return ("exclamationmark.triangle", "Voice input needs attention")
        }
    }
}

/// Replaces the composer's buttons while a dictation runs: cancel, timer,
/// level meter and stop while recording; progress while transcribing and
/// cleaning up; the reason with Retry after a failure.
struct VoiceDictationBar: View {
    let voice: VoiceInputModel

    @Environment(\.mobaideColors) private var colors
    @Environment(\.openURL) private var openURL
    @State private var details: String?

    var body: some View {
        HStack(spacing: 8) {
            content
        }
        .font(.body)
        .accessibilityIdentifier("voice_dictation_bar")
        .alert("Details", isPresented: detailsPresented) {
            Button("Close", role: .cancel) {}
        } message: {
            Text(details ?? "")
        }
    }

    @ViewBuilder
    private var content: some View {
        switch voice.dictation {
        case .idle, .result:
            EmptyView()
        case .starting:
            Text("Opening the microphone…").frame(maxWidth: .infinity, alignment: .leading)
            cancelButton
        case .recording(let elapsedMs, let level, let remaining, let lagging):
            cancelButton
            Rectangle().fill(colors.danger).frame(width: 10, height: 10).accessibilityHidden(true)
            Text(VoiceStatusView.formatDuration(ms: elapsedMs))
                .monospacedDigit()
                .accessibilityLabel("Recording, \(elapsedMs / 1000) seconds")
            LevelMeter(level: level)
            if let remaining {
                Text("\(remaining) s").foregroundStyle(colors.warning).monospacedDigit()
            } else if lagging {
                Text("Slow").foregroundStyle(colors.warning)
            }
            Button {
                voice.stopDictation()
            } label: {
                Image(systemName: "stop.fill")
            }
            .buttonStyle(.pixelSmallPrimary)
            .accessibilityLabel("Stop and transcribe")
            .accessibilityIdentifier("voice_stop_button")
        case .transcribing(let reason, let lagging):
            PixelLoadingBlock(width: 8, height: 12)
            Text(Self.transcribingLine(reason: reason, lagging: lagging, agent: voice.agentName))
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
            cancelButton
        // Part of transcribing to the user: the same bar, one line changed.
        case .cleaning:
            PixelLoadingBlock(width: 8, height: 12)
            Text("Cleaning up the text…")
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
            cancelButton
        case .empty:
            Text("No speech was recognised.").frame(maxWidth: .infinity, alignment: .leading)
            Button("Again") { voice.startDictation() }.buttonStyle(.pixelSmall)
            closeButton
        case .failed(let code, let retryable, let message, let hasAudio):
            Text(VoiceErrorCopy.dictation(code, agent: voice.agentName))
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
            if code == VoiceCodes.micPermissionDenied {
                Button("Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
                .buttonStyle(.pixelSmall)
            } else if retryable && hasAudio {
                Button {
                    voice.retryDictation()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.pixelSmall)
                .accessibilityLabel("Retry from the recording")
            }
            if let message, !message.isEmpty {
                Button("Details") { details = message }
            }
            closeButton
        }
    }

    private var cancelButton: some View {
        Button {
            voice.cancelDictation()
        } label: {
            Image(systemName: "xmark")
        }
        .accessibilityLabel("Cancel dictation")
        .accessibilityIdentifier("voice_cancel_button")
    }

    private var closeButton: some View {
        Button {
            voice.dismissDictation()
        } label: {
            Image(systemName: "xmark")
        }
        .accessibilityLabel("Close")
    }

    private var detailsPresented: Binding<Bool> {
        Binding(get: { details != nil }, set: { if !$0 { details = nil } })
    }

    private static func transcribingLine(reason: InterruptReason?, lagging: Bool, agent: String) -> String {
        if lagging { return "Waiting for the connection…" }
        switch reason {
        case .background: return "Stopped when the app left the screen. Transcribing…"
        case .call: return "Stopped for a call. Transcribing…"
        case .device: return "The microphone went away. Transcribing…"
        case .limit: return "Time limit reached. Transcribing…"
        case nil: return "Transcribing on \(agent)…"
        }
    }
}

private struct LevelMeter: View {
    let level: Float

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Rectangle().fill(colors.surface2)
                Rectangle()
                    .fill(colors.accent)
                    .frame(width: proxy.size.width * CGFloat(min(max(level, 0), 1)))
            }
        }
        .frame(height: 8)
        .accessibilityElement()
        .accessibilityLabel("Microphone level")
        .accessibilityValue("\(Int(level * 100)) percent")
    }
}

/// What the mic opens while voice is not ready; follows the live status.
/// Never opens on its own.
struct VoiceSetupSheet: View {
    let voice: VoiceInputModel

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VoicePanel(voice: voice, settings: false)
                    .padding(16)
            }
            .navigationTitle("Voice input")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .pixelSheet()
        .accessibilityIdentifier("voice_setup_sheet")
        .task(id: voice.status?.state) { await voice.pollWhileQueued() }
    }
}

/// Voice input for the connected agent in every state, with the real costs the
/// agent reports. `settings` adds remove, reinstall, the mic toggle and the
/// model's license (Settings → Voice input); without it this is the enable
/// sheet.
struct VoicePanel: View {
    let voice: VoiceInputModel
    let settings: Bool

    @Environment(\.mobaideColors) private var colors
    @State private var confirmRemove = false
    @State private var showLicense = false
    @State private var details: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let status = voice.status {
                stateContent(status)
                if let error = voice.actionError {
                    Text(error).font(.caption).foregroundStyle(colors.danger)
                }
                if settings || status.state == VoiceStatusResponse.stateNotInstalled {
                    modelFacts(status.model)
                }
                if settings && status.state != VoiceStatusResponse.stateUnsupported {
                    Toggle("Show the mic in the command field", isOn: showMicBinding)
                        .toggleStyle(.pixelCheckbox)
                }
                if cleanupToggleShown(status) {
                    VoiceCleanupToggle(voice: voice)
                }
                if settings, let notice = status.model.notice, !notice.isEmpty {
                    // Borderless: in a List row, default-style buttons all fire on any tap.
                Button("Model license") { showLicense = true }
                    .buttonStyle(.borderless)
                        .sheet(isPresented: $showLicense) { LicenseSheet(notice: notice) }
                }
            } else {
                Text("Checking voice input on \(voice.agentName)…")
            }
        }
        .font(.body)
        .frame(maxWidth: .infinity, alignment: .leading)
        .alert("Details", isPresented: detailsPresented) {
            Button("Close", role: .cancel) {}
        } message: {
            Text(details ?? "")
        }
        .confirmationDialog("Remove voice input?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove", role: .destructive) { voice.remove() }
            Button("Keep", role: .cancel) {}
        } message: {
            if let status = voice.status {
                Text("This frees about \(VoiceStatusView.formatBytes(status.model.disk_bytes)) on \(voice.agentName).")
            }
        }
    }

    @ViewBuilder
    private func stateContent(_ status: VoiceStatusResponse) -> some View {
        let lowResources = status.host.recommendation == VoiceStatusResponse.recommendationLowResources
        switch status.state {
        case VoiceStatusResponse.stateNotInstalled:
            Text(
                "Voice input on \(voice.agentName) downloads about \(VoiceStatusView.formatBytes(status.model.download_bytes)) "
                    + "once, then uses about \(VoiceStatusView.formatBytes(status.model.disk_bytes)) of disk and about "
                    + "\(VoiceStatusView.formatBytes(status.model.ram_bytes)) of memory while you dictate."
            )
            if lowResources { lowResourcesWarning(status.host.reasons) }
            Text(
                "\(voice.agentName) transcribes what you say. The audio goes to it through the Mobaide relay, or "
                    + "straight to it in direct-connection mode, and is never stored. The text lands in the command "
                    + "field for you to review and is never sent on its own."
            )
            .font(.caption)
            .foregroundStyle(colors.textMuted)
            HStack(spacing: 8) {
                Button("Enable voice input") { voice.install() }
                    .buttonStyle(.pixelPrimary)
                    .disabled(voice.actionBusy)
                    .accessibilityIdentifier("voice_enable_button")
                if !settings {
                    Button("Not now") { voice.hideSetup() }.buttonStyle(.pixel)
                }
            }
            if !settings {
                Button("Hide the mic for this agent") { voice.setMicHidden(true) }
                    .font(.caption)
            }
        case VoiceStatusResponse.stateQueued, VoiceStatusResponse.stateDownloading, VoiceStatusResponse.stateVerifying:
            Text(VoiceStatusView.preparingLabel(status))
            PixelProgressBar(percent: VoiceStatusView.progressPercent(status))
            Text(
                status.repairing
                    ? "Your voice model stays; only a small update is downloading."
                    : "You can close this. The mic turns on by itself when voice input is ready."
            )
            .font(.caption)
            .foregroundStyle(colors.textMuted)
            Button(status.repairing ? "Stop" : "Cancel") { voice.cancelInstall() }
                .buttonStyle(.pixel)
                .disabled(voice.actionBusy)
        case VoiceStatusResponse.stateReady:
            Text("Voice input is ready on \(voice.agentName).")
            if settings {
                HStack(spacing: 8) {
                    Button("Reinstall") { voice.reinstall() }.buttonStyle(.pixel).disabled(voice.actionBusy)
                    Button("Remove") { confirmRemove = true }.buttonStyle(.pixelDanger).disabled(voice.actionBusy)
                }
            }
        case VoiceStatusResponse.stateFailed:
            Text(VoiceErrorCopy.install(status.error?.code ?? "", repairing: status.repairing))
            // The agent's raw message can carry host details: behind Details only.
            if let message = status.error?.message, !message.isEmpty {
                Button("Details") { details = message }
                    .buttonStyle(.borderless)
                    .font(.caption)
            }
            if lowResources { lowResourcesWarning(status.host.reasons) }
            HStack(spacing: 8) {
                Button("Retry") { voice.install() }.buttonStyle(.pixelPrimary).disabled(voice.actionBusy)
                if settings {
                    Button("Remove") { confirmRemove = true }.buttonStyle(.pixelDanger).disabled(voice.actionBusy)
                }
            }
        default:
            Text("\(voice.agentName) can't run voice input.")
            ForEach(status.host.reasons, id: \.code) { reason in
                Text("• \(reason.message)").font(.caption)
            }
        }
    }

    /// In Settings for an agent that supports cleanup; in the enable sheet once
    /// voice is enabled in a session that can use it, so the first dictation is
    /// already cleaned up.
    private func cleanupToggleShown(_ status: VoiceStatusResponse) -> Bool {
        if settings {
            return voice.cleanupSupported && status.state != VoiceStatusResponse.stateUnsupported
        }
        return voice.cleanupOffered && [
            VoiceStatusResponse.stateQueued,
            VoiceStatusResponse.stateDownloading,
            VoiceStatusResponse.stateVerifying,
            VoiceStatusResponse.stateReady,
        ].contains(status.state)
    }

    private func lowResourcesWarning(_ reasons: [VoiceHostReason]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("This computer may struggle with voice input.").fontWeight(.semibold)
            ForEach(reasons, id: \.code) { reason in
                Text("• \(reason.message)").font(.caption)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelRing(colors.warning)
    }

    private func modelFacts(_ model: VoiceModelInfo) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Model: \(model.name)")
            Text("Languages: \(VoiceStatusView.languageNames(model.languages).joined(separator: ", "))")
        }
        .font(.caption)
        .foregroundStyle(colors.textMuted)
    }

    private var detailsPresented: Binding<Bool> {
        Binding(get: { details != nil }, set: { if !$0 { details = nil } })
    }

    private var showMicBinding: Binding<Bool> {
        Binding(get: { !voice.micHidden }, set: { voice.setMicHidden(!$0) })
    }
}

/// The transcript cleanup checkbox. The first time it is turned on, a notice
/// says where the text goes, and the setting changes only after "Got it".
private struct VoiceCleanupToggle: View {
    let voice: VoiceInputModel

    @Environment(\.mobaideColors) private var colors
    @State private var noticeShown = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle(VoiceCleanupCopy.toggleLabel, isOn: binding)
                .toggleStyle(.pixelCheckbox)
                .accessibilityIdentifier("voice_cleanup_checkbox")
                .alert(VoiceCleanupCopy.noticeTitle, isPresented: $noticeShown) {
                    Button("Got it") { voice.setCleanUp(true) }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text(VoiceCleanupCopy.notice(agent: voice.agentName))
                }
            if let error = voice.cleanupSaveError {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(colors.danger)
                    .accessibilityIdentifier("voice_cleanup_error")
            }
        }
    }

    private var binding: Binding<Bool> {
        Binding(
            get: { voice.cleanUp },
            set: { on in
                if !on {
                    voice.setCleanUp(false)
                } else if voice.cleanupNoticeSeen {
                    voice.setCleanUp(true)
                } else {
                    noticeShown = true
                }
            }
        )
    }
}

enum VoiceCleanupCopy {
    static let toggleLabel =
        "Clean up dictated text with the session's AI agent: remove filler words, fix punctuation and technical terms"
    static let noticeTitle = "Clean up dictated text"

    static func notice(agent: String) -> String {
        "The session's AI agent on \(agent) cleans up each transcript with its cheapest model: Claude Haiku "
            + "for Claude Code, GPT Luna for Codex, Gemini Flash for Antigravity; OpenCode uses its default model; "
            + "GitHub Copilot uses its default model. It runs under your own account: the text goes to that "
            + "agent's provider and counts toward your usage. Mobaide doesn't store it."
    }

    static func saveFailed(agent: String) -> String {
        "Couldn't save this setting on \(agent). Try again."
    }
}

private struct LicenseSheet: View {
    let notice: String

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                Text(notice)
                    .font(MobaideTypography.code)
                    .textSelection(.enabled)
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .navigationTitle("Model license")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .pixelSheet()
    }
}

/// Settings → Voice input, for the connected agent.
struct VoiceSettingsSection: View {
    let voice: VoiceInputModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Section {
            if !voice.capabilityKnown {
                Text("Checking the connected agent…").foregroundStyle(colors.textMuted)
            } else if voice.capability == nil {
                Text(
                    "Dictate into the command field: your own computer transcribes what you say. "
                        + "Connect to an agent that supports voice input (it updates itself) to set it up."
                )
                .font(.caption)
                .foregroundStyle(colors.textMuted)
            } else {
                VoicePanel(voice: voice, settings: true)
                    .padding(.vertical, 4)
            }
        } header: {
            PixelSectionHeader("Voice input")
        }
        .pixelFormSection()
        .task { await voice.load() }
        .task(id: voice.status?.state) { await voice.pollWhileQueued() }
    }
}
