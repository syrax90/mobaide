import SwiftUI

// Per-agent option rows shared by a normal session's config step and the Auto
// Scan step, which drive them from separate option buckets through bindings.
// Each body is a flat list of rows, so a `Section` lays them out one per row.

/// Codex's sandbox × approval shortcuts.
struct CodexAutonomyRows: View {
    let preset: CodexAutonomyPreset?
    let onSelect: (CodexAutonomyPreset) -> Void

    var body: some View {
        ForEach(CodexAutonomyPreset.allCases, id: \.self) { candidate in
            SelectableRow(title: candidate.title, subtitle: candidate.subtitle, selected: preset == candidate) {
                onSelect(candidate)
            }
        }
    }
}

struct CodexModelEffortRows: View {
    let model: Binding<String?>
    let effort: Binding<String>
    /// With the keep-selected repair already applied (`codexModelOptions(selected:)`).
    let modelOptions: [String]
    let advertisedEfforts: [String]

    var body: some View {
        Picker("Model", selection: model) {
            Text("Default").tag(String?.none)
            ForEach(modelOptions, id: \.self) { Text($0).tag(String?.some($0)) }
        }
        // Keep-selected repair: a restored or late-probe effort outside the
        // advertised set must stay in the options, or the Picker renders blank
        // while the request still sends the value.
        let options = advertisedEfforts.contains(effort.wrappedValue)
            ? advertisedEfforts : advertisedEfforts + [effort.wrappedValue]
        Picker("Reasoning effort", selection: effort) {
            ForEach(options, id: \.self) { Text($0).tag($0) }
        }
    }
}

struct CodexSandboxApprovalRows: View {
    let sandbox: Binding<String>
    let approval: Binding<String>
    let fullAuto: Bool

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Picker("Sandbox", selection: sandbox) {
            ForEach(CreateSessionModel.codexSandboxes, id: \.self) { Text($0).tag($0) }
        }
        .disabled(fullAuto)
        Picker("Approval", selection: approval) {
            ForEach(CreateSessionModel.codexApprovals, id: \.self) { Text($0).tag($0) }
        }
        .disabled(fullAuto)
        if fullAuto {
            Text("Sandbox and approval are ignored in Full-access (--yolo).")
                .font(.footnote)
                .foregroundStyle(colors.textMuted)
        }
    }
}

/// `skipPermissions` and `sandbox` must reach setters that keep the pair
/// mutually exclusive.
struct AntigravityOptionRows: View {
    let model: Binding<String?>
    let models: [String]
    let skipPermissions: Binding<Bool>
    let sandbox: Binding<Bool>
    let addDirs: Binding<String>
    /// Shown right under the Skip permissions toggle (Auto Scan's ask hint).
    var skipPermissionsHint: String?

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        // Keep-selected repair: a model from a template or the workspace that
        // the PC does not advertise must stay selectable, or the Picker shows
        // nothing while the request still sends it.
        let options = model.wrappedValue.map { models.contains($0) ? models : models + [$0] } ?? models
        Picker("Model", selection: model) {
            Text("Auto").tag(String?.none)
            ForEach(options, id: \.self) { Text($0).tag(String?.some($0)) }
        }
        Toggle("Skip permissions (dangerous)", isOn: skipPermissions)
        if let skipPermissionsHint {
            Text(skipPermissionsHint)
                .font(.footnote)
                .foregroundStyle(colors.warning)
        }
        Toggle("Sandbox", isOn: sandbox)
        let error = CreateSessionModel.addDirsError(addDirs.wrappedValue)
        TextField("Additional directories", text: addDirs, axis: .vertical)
            .lineLimit(1...4)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .pixelField(isInvalid: error != nil)
        Text(error ?? "One per line, or comma-separated.")
            .font(.footnote)
            .foregroundStyle(error != nil ? colors.danger : colors.textMuted)
    }
}

struct OpenCodeOptionRows: View {
    let autoApprove: Binding<Bool>

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Toggle("Auto-approve permissions (dangerous)", isOn: autoApprove)
        Text("OpenCode accepts every request that your rules do not deny.")
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
    }
}

struct CopilotOptionRows: View {
    let allowAll: Binding<Bool>

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Toggle("Allow all tools, paths and URLs (dangerous)", isOn: allowAll)
        Text("When on, Copilot does not ask before it runs commands, edits files or opens URLs.")
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
    }
}

struct CursorOptionRows: View {
    let force: Binding<Bool>

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Toggle("Run everything without asking (dangerous)", isOn: force)
        Text("When on, Cursor runs commands without asking, except commands that its settings deny.")
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
    }
}

struct JunieOptionRows: View {
    let planMode: Binding<Bool>
    let brave: Binding<Bool>

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        Toggle("Start in Plan mode", isOn: planMode)
        Text("Junie prepares a plan and asks you to approve it before it changes your files.")
            .font(.footnote)
            .foregroundStyle(colors.textMuted)
        Toggle("Brave mode (dangerous)", isOn: brave)
        Text(
            brave.wrappedValue
                ? "Junie runs all commands without asking, in this session only."
                : "Junie uses its own Brave setting. By default it asks before commands that are not safe."
        )
        .font(.footnote)
        .foregroundStyle(colors.textMuted)
    }
}
