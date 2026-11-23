import SwiftUI

/// The first mic tap on an agent with no engine choice: where the speech is
/// turned into text. Mobaide Cloud is preselected; nothing is recorded or sent
/// before the user confirms. Choosing the computer also starts its download,
/// whose costs the card shows.
struct VoiceEngineChooser: View {
    let voice: VoiceInputModel
    let cloud: CloudVoiceStatus

    @Environment(\.mobaideColors) private var colors
    @State private var picked = VoiceEngine.cloud

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Choose where your speech is turned into text for \(Text(voice.agentName).bold()).")
            VStack(spacing: 8) {
                VoiceEngineCard(
                    title: CloudVoiceCopy.cloudName,
                    badge: CloudVoiceCopy.recommended,
                    selected: selected == .cloud,
                    identifier: "voice_engine_cloud"
                ) {
                    picked = .cloud
                } details: {
                    Text(CloudVoiceCopy.cloudSummary)
                    Text(CloudVoiceCopy.cloudDetails(cloud))
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                } footer: {
                    PrivacyPolicyLink()
                }
                VoiceEngineCard(
                    title: CloudVoiceCopy.localName,
                    selected: selected == .local,
                    identifier: "voice_engine_local"
                ) {
                    picked = .local
                } details: {
                    localDetails
                }
                .disabled(blocker != nil)
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel(CloudVoiceCopy.engineLabel)
            Text("\(CloudVoiceCopy.reviewNote) You can change this later in Settings → Voice input.")
                .font(.caption)
                .foregroundStyle(colors.textMuted)
            if voice.engineSaveFailed {
                Text(CloudVoiceCopy.choiceSaveFailed)
                    .font(.caption)
                    .foregroundStyle(colors.danger)
                    .accessibilityIdentifier("voice_engine_error")
            }
            HStack(spacing: 8) {
                Button("Continue") { voice.confirmEngine(selected) }
                    .buttonStyle(.pixelPrimary)
                    .disabled(voice.engineSaving)
                    .accessibilityIdentifier("voice_engine_continue_button")
                Button("Not now") { voice.hideSetup() }.buttonStyle(.pixel)
            }
            VoiceTextButton("Hide the mic for this agent") { voice.setMicHidden(true) }
        }
        .font(.body)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("voice_engine_chooser")
    }

    private var blocker: String? { voice.localBlocker }

    /// The computer can stop being a choice while the sheet is open.
    private var selected: VoiceEngine { blocker == nil ? picked : .cloud }

    @ViewBuilder
    private var localDetails: some View {
        if let blocker {
            Text(blocker)
                .font(.caption)
                .foregroundStyle(colors.textMuted)
        } else {
            Text(CloudVoiceCopy.localSummary(agent: voice.agentName))
            if let status = voice.status {
                Text(CloudVoiceCopy.localDetails(agent: voice.agentName, status: status))
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
        }
    }
}

/// One choice of a radio group, as a card: the radio square, a title with an
/// optional badge, and what the choice means. `footer` sits inside the card
/// and outside the button, so a link in it takes its own taps.
private struct VoiceEngineCard<Details: View, Footer: View>: View {
    let title: String
    var badge: String?
    let selected: Bool
    let identifier: String
    let action: () -> Void
    @ViewBuilder let details: Details
    @ViewBuilder let footer: Footer

    @Environment(\.mobaideColors) private var colors
    @Environment(\.isEnabled) private var isEnabled

    private static var textInset: CGFloat { PixelMetrics.radio + 10 }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: action) {
                HStack(alignment: .top, spacing: 10) {
                    PixelRadio(isSelected: selected)
                        .padding(.top, 2)
                    VStack(alignment: .leading, spacing: 4) {
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 8) { heading }
                            VStack(alignment: .leading, spacing: 4) { heading }
                        }
                        details
                    }
                    Spacer(minLength: 0)
                }
                .frame(minHeight: 44, alignment: .top)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityIdentifier(identifier)
            footer
                .padding(.leading, Self.textInset)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelCard(selected: selected)
    }

    @ViewBuilder
    private var heading: some View {
        Text(title)
            .fontWeight(.semibold)
            .foregroundStyle(isEnabled ? colors.text : colors.gray)
        if let badge {
            PixelChip(badge, .filled)
        }
    }
}

extension VoiceEngineCard where Footer == EmptyView {
    init(
        title: String,
        badge: String? = nil,
        selected: Bool,
        identifier: String,
        action: @escaping () -> Void,
        @ViewBuilder details: () -> Details
    ) {
        self.init(
            title: title,
            badge: badge,
            selected: selected,
            identifier: identifier,
            action: action,
            details: details,
            footer: { EmptyView() }
        )
    }
}

/// Borderless: in a List row, default-style buttons all fire on any tap.
private struct PrivacyPolicyLink: View {
    var body: some View {
        if let url = URL(string: AppConfig.privacyPolicyURL) {
            Link(destination: url) {
                Text("Privacy policy").voiceTextTarget()
            }
            .font(.caption)
            .buttonStyle(.borderless)
            .padding(.vertical, VoiceTextButton.giveBack)
        }
    }
}

/// Cloud voice input for the connected agent: ready, unavailable or out of
/// today's allowance. Without `settings` it is what the mic opens while cloud
/// voice input cannot record; with it, Settings → Voice input, which adds
/// today's use, the limits, where the recordings go and the mic toggle.
struct CloudVoicePanel: View {
    let voice: VoiceInputModel
    let cloud: CloudVoiceStatus
    let settings: Bool

    @Environment(\.mobaideColors) private var colors
    /// "Try again" was tapped and its answer is not in yet.
    @State private var rechecking = false
    /// "Try again" found the service still down: say so, or the tap looks
    /// like one that did nothing.
    @State private var rechecked = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            stateContent
            if settings {
                VStack(alignment: .leading, spacing: 2) {
                    if CloudVoiceCopy.hasDailyLimit(cloud) {
                        Text("Used today: \(usage)")
                    }
                    Text("Recording: Up to \(CloudVoiceCopy.formatAllowance(seconds: cloud.limits.max_seconds)) each")
                }
                .font(.caption)
                .foregroundStyle(colors.textMuted)
                VStack(alignment: .leading, spacing: 4) {
                    Text(CloudVoiceCopy.settingsPrivacyNote)
                        .font(.caption)
                        .foregroundStyle(colors.textMuted)
                    PrivacyPolicyLink()
                }
                VoiceMicToggle(voice: voice)
            }
        }
        .font(.body)
        .voicePanelWrapping()
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("cloud_voice_panel")
        .onChange(of: cloud.state) { _, _ in rechecked = false }
    }

    private var limit: String { CloudVoiceCopy.formatAllowance(seconds: cloud.usage.limit_seconds) }

    private var usage: String {
        CloudVoiceCopy.formatUsage(usedSeconds: cloud.usage.used_seconds, limitSeconds: cloud.usage.limit_seconds)
    }

    private var checking: Bool { rechecking || voice.cloudChecking }

    private func recheck() {
        guard !rechecking else { return }
        rechecking = true
        Task {
            await voice.recheckCloudNow()
            rechecking = false
            guard voice.cloud?.state == CloudVoiceStatus.stateUnavailable else { return }
            rechecked = true
            AccessibilityNotification.Announcement(CloudVoiceCopy.stillUnavailable).post()
        }
    }

    /// Switching to the computer is offered where the mic sent the user here,
    /// for an agent with voice of its own; Settings has the selector for it.
    private var offersLocal: Bool { !settings && voice.localSupported }

    private var meanwhile: String {
        offersLocal ? " You can dictate with \(voice.agentName) itself meanwhile." : ""
    }

    @ViewBuilder
    private var stateContent: some View {
        switch cloud.state {
        case CloudVoiceStatus.stateUnavailable:
            Text("Cloud voice input is unavailable right now.")
            // One slot for both lines: a note added under the buttons would
            // move them under the finger on every retry.
            Text("\(unavailableLine)\(meanwhile)")
                .font(.caption)
                .foregroundStyle(colors.textMuted)
                .accessibilityIdentifier("cloud_voice_unavailable_note")
            HStack(spacing: 8) {
                Button(checking ? "Checking…" : "Try again") { recheck() }
                    .buttonStyle(.pixelPrimary)
                    .disabled(checking)
                    .accessibilityIdentifier("cloud_voice_retry_button")
                useLocalButton
            }
        case CloudVoiceStatus.stateLimitReached:
            Text("You've used today's \(limit) of cloud voice input.")
            Text("\(resetLine)\(meanwhile)")
                .font(.caption)
                .foregroundStyle(colors.textMuted)
            useLocalButton
        default:
            Text(
                settings
                    ? "Mobaide Cloud turns your speech into text for \(voice.agentName)."
                    : "Cloud voice input is ready. Close this and tap the mic to dictate."
            )
        }
    }

    private var unavailableLine: String {
        rechecked && !checking
            ? CloudVoiceCopy.stillUnavailable
            : "Mobaide's speech service isn't answering. This is usually short."
    }

    private var resetLine: String {
        let resetAt = CloudVoiceCopy.resetTimeLabel(cloud.usage.resets_at)
        return resetAt.isEmpty ? "It resets tomorrow." : "It resets at \(resetAt)."
    }

    @ViewBuilder
    private var useLocalButton: some View {
        if offersLocal {
            Button("Use this computer instead") { voice.useLocal() }
                .buttonStyle(.pixel)
                .disabled(voice.engineSaving)
        }
    }
}

/// "Speech recognition" in Settings → Voice input: Mobaide Cloud or the
/// computer. Stored on the central server, so every device follows.
struct VoiceEngineSetting: View {
    let voice: VoiceInputModel

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(CloudVoiceCopy.engineLabel).sectionHeading()
            VStack(alignment: .leading, spacing: 0) {
                option(.cloud, title: CloudVoiceCopy.cloudName, badge: CloudVoiceCopy.recommended)
                // A choice that cannot be made says why, as its card in the chooser does.
                option(.local, title: CloudVoiceCopy.localName, badge: nil, note: localDisabledBy)
                    .disabled(localDisabledBy != nil)
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel(CloudVoiceCopy.engineLabel)
            if voice.engineSaveFailed {
                Text(CloudVoiceCopy.settingSaveFailed)
                    .font(.caption)
                    .foregroundStyle(colors.danger)
                    .accessibilityIdentifier("voice_engine_error")
            }
        }
    }

    /// An agent already on its own voice keeps the option, whatever its state now.
    private var localDisabledBy: String? {
        voice.route == .local ? nil : voice.localBlocker
    }

    private func option(_ engine: VoiceEngine, title: String, badge: String?, note: String? = nil) -> some View {
        let selected = voice.route == (engine == .cloud ? .cloud : .local)
        return EngineOptionRow(title: title, badge: badge, note: note, selected: selected) {
            voice.pickEngine(engine)
        }
        .accessibilityIdentifier("voice_engine_setting_\(engine.rawValue)")
    }
}

/// Plain style: in a List row, default-style buttons all fire on any tap.
private struct EngineOptionRow: View {
    let title: String
    let badge: String?
    /// Under the title, in line with it: why the option is disabled.
    let note: String?
    let selected: Bool
    let action: () -> Void

    @Environment(\.mobaideColors) private var colors
    @Environment(\.isEnabled) private var isEnabled

    /// The note is outside the button: a disabled button dims its whole
    /// label, and the reason must stay readable. It shows only while the
    /// option is disabled, so that row needs no 44 pt target.
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: action) {
                HStack(spacing: 10) {
                    PixelRadio(isSelected: selected)
                    // At large text sizes the badge goes under the title.
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) { heading }
                        VStack(alignment: .leading, spacing: 4) { heading }
                    }
                    Spacer(minLength: 0)
                }
                .frame(minHeight: note == nil ? 44 : nil)
                .padding(.top, note == nil ? 0 : 11)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if let note {
                Text(note)
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                    .multilineTextAlignment(.leading)
                    .padding(.leading, PixelMetrics.radio + 10)
                    .padding(.top, 2)
                    .padding(.bottom, 6)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    @ViewBuilder
    private var heading: some View {
        Text(title).foregroundStyle(isEnabled ? colors.text : colors.gray)
        if let badge {
            PixelChip(badge, .filled).fixedSize()
        }
    }
}
