import SwiftUI

/// Limits the agent applies to Herdr input; checked here so the user sees them
/// before the request.
enum HerdrLimits {
    static let labelMaxChars = 80
}

/// What "New workspace" and "New tab" collect.
struct HerdrCreateValues: Equatable {
    let cwd: String?
    let label: String?
    let agent: String?
}

/// New workspace or new tab (Android `HerdrCreateDialog`). The folder comes
/// from the agent's projects, with a free path as the last choice, so the
/// usual case is two taps. A tab opens in its workspace's folder and asks for
/// none.
struct HerdrCreateSheet: View {
    let forWorkspace: Bool
    /// What the server calls a top-level group: "workspace", or "terminal" on herdr-core.
    let noun: String
    let projects: [ProjectInfo]
    let agents: [HerdrAgentKind]
    let busy: Bool
    /// Why the last try failed; shown here, where the user looks.
    var error: String? = nil
    let onCreate: (HerdrCreateValues) -> Void

    private enum Folder: Hashable {
        case standard
        case project(String)
        case custom
    }

    @Environment(\.dismiss) private var dismiss
    @State private var folder = Folder.standard
    @State private var customPath = ""
    @State private var label = ""
    @State private var agent: String?
    @FocusState private var pathFocused: Bool

    private var trimmedPath: String {
        customPath.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A full path: POSIX, home-relative, or a Windows drive.
    static func isFullPath(_ path: String) -> Bool {
        if path.hasPrefix("/") || path.hasPrefix("~") { return true }
        return path.range(of: #"^[A-Za-z]:[\\/]"#, options: .regularExpression) != nil
    }

    private var pathInvalid: Bool {
        folder == .custom && !trimmedPath.isEmpty && !Self.isFullPath(trimmedPath)
    }

    private var canSubmit: Bool {
        !busy && !pathInvalid && !(forWorkspace && folder == .custom && trimmedPath.isEmpty)
    }

    private var cwd: String? {
        guard forWorkspace else { return nil }
        switch folder {
        case .standard: return nil
        case .project(let path): return path
        case .custom: return trimmedPath.isEmpty ? nil : trimmedPath
        }
    }

    var body: some View {
        NavigationStack {
            List {
                if let error {
                    Section {
                        PixelErrorBanner(error)
                            .pixelListRow()
                            .accessibilityIdentifier("herdr-create-error")
                    }
                }
                if forWorkspace {
                    Section(header: PixelSectionHeader("Folder")) {
                        Picker("Folder", selection: $folder) {
                            Text("Default folder").tag(Folder.standard)
                            ForEach(projects, id: \.name) { project in
                                Text(project.name).tag(Folder.project(project.path))
                            }
                            Text("Another folder…").tag(Folder.custom)
                        }
                        .labelsHidden()
                        .disabled(busy)
                        .accessibilityIdentifier("herdr-create-folder")
                        if folder == .custom {
                            PixelLabeledField(
                                label: "Folder path",
                                error: pathInvalid ? "Enter a full path, for example /home/me/project." : nil
                            ) {
                                TextField("/home/me/project", text: $customPath)
                                    .focused($pathFocused)
                                    .textInputAutocapitalization(.never)
                                    .autocorrectionDisabled()
                                    .disabled(busy)
                                    .pixelField(isInvalid: pathInvalid, isFocused: pathFocused)
                                    .accessibilityIdentifier("herdr-create-path")
                            }
                        }
                    }
                    .pixelFormSection()
                }
                Section(header: PixelSectionHeader("Name (optional)")) {
                    TextField("Name", text: $label)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .disabled(busy)
                        .pixelField()
                        .onChange(of: label) { _, value in
                            if value.count > HerdrLimits.labelMaxChars {
                                label = String(value.prefix(HerdrLimits.labelMaxChars))
                            }
                        }
                        .accessibilityIdentifier("herdr-create-name")
                }
                .pixelFormSection()
                if !agents.isEmpty {
                    Section(header: PixelSectionHeader("Start an agent")) {
                        Picker("Start an agent", selection: $agent) {
                            Text("None (a terminal)").tag(String?.none)
                            ForEach(agents, id: \.id) { kind in
                                Text(kind.label).tag(Optional(kind.id))
                            }
                        }
                        .labelsHidden()
                        .disabled(busy)
                        .accessibilityIdentifier("herdr-create-agent")
                    }
                    .pixelFormSection()
                }
            }
            .pixelForm()
            .navigationTitle(forWorkspace ? "New \(noun)" : "New tab")
            .navigationBarTitleDisplayMode(.inline)
            .interactiveDismissDisabled(busy)
            .onChange(of: folder) { _, value in
                if value == .custom { pathFocused = true }
            }
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(busy)
                }
                ToolbarItem(placement: .confirmationAction) {
                    // No `dismiss()`: the model closes the sheet when the
                    // action is done, and a failed one keeps what was typed.
                    Button(busy ? "Creating…" : "Create") {
                        let name = label.trimmingCharacters(in: .whitespacesAndNewlines)
                        onCreate(HerdrCreateValues(cwd: cwd, label: name.isEmpty ? nil : name, agent: agent))
                    }
                    .disabled(!canSubmit)
                    .accessibilityIdentifier("herdr-create-submit")
                }
            }
        }
        .presentationDetents([.medium, .large])
        .pixelSheet()
    }
}
