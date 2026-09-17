import SwiftUI

/// The Rebase sheet (Android `GitRebaseSheet`, web `GitRebaseDialog`): pick
/// the branch to replay this one on top of. A project-backed session picks
/// from the project's local and remote-tracking branches, deliberately with no
/// preselection for a history rewrite; any other session — or a list that
/// failed to load — types the ref. What would make git refuse to start is
/// explained up front and keeps the button disabled; the agent still decides.
struct GitRebaseSheet: View {
    @Bindable var model: GitChangesModel

    @Environment(\.mobaideColors) private var colors
    @FocusState private var ontoFocused: Bool
    @State private var picked: String?
    @State private var typed = ""

    private var sheet: RebaseSheetState { model.rebaseSheet ?? RebaseSheetState() }
    private var busy: Bool { sheet.isStarting }
    private var blocker: String? { GitChangesDisplay.rebaseBlocker(model.status) }

    private var targets: [GitChangesDisplay.RebaseTarget] {
        guard case .ready(let items) = sheet.targets else { return [] }
        return GitChangesDisplay.rebaseTargets(items, currentBranch: model.repo?.head.branch)
    }

    private var onto: String? {
        switch sheet.targets {
        case .ready: return picked
        case .typed:
            let trimmed = typed.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        case .loading: return nil
        }
    }

    private var submittable: Bool { !busy && blocker == nil && onto != nil }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Replays this branch's commits on top of the branch you pick.")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                    field
                    if let blocker {
                        PixelErrorBanner(blocker)
                            .accessibilityIdentifier("git-rebase-blocker")
                    }
                    if let error = sheet.error {
                        VStack(alignment: .leading, spacing: 8) {
                            PixelErrorBanner(error.message)
                            if let output = error.output {
                                ScrollView(.vertical) {
                                    Text(output)
                                        .font(MobaideTypography.code)
                                        .textSelection(.enabled)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                }
                                .frame(maxHeight: 160)
                                .pixelCodePlate()
                            }
                        }
                        .accessibilityIdentifier("git-rebase-error")
                    }
                }
                .padding(16)
            }
            .pixelScreenBackground()
            .navigationTitle(model.repo.map { "Rebase \(GitChangesDisplay.headLabel($0))" } ?? "Rebase")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { model.closeRebase() }
                        .disabled(busy)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(busy ? "Rebasing…" : "Rebase") {
                        guard let onto else { return }
                        Task { await model.startRebase(onto: onto) }
                    }
                    .disabled(!submittable)
                    .accessibilityLabel("Rebase")
                    .accessibilityIdentifier("git-rebase-submit")
                }
            }
        }
        .pixelSheet()
        .interactiveDismissDisabled(busy)
    }

    @ViewBuilder
    private var field: some View {
        PixelLabeledField(label: "Onto") {
            switch sheet.targets {
            case .loading:
                LoadingIndicator()
            case .ready:
                if targets.isEmpty {
                    Text("No other branches in this project.")
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                } else {
                    Picker("Onto", selection: $picked) {
                        Text("Choose a branch").tag(String?.none)
                        ForEach(targets) { target in
                            Text(target.label).tag(Optional(target.value))
                        }
                    }
                    .labelsHidden()
                    .disabled(busy)
                    .accessibilityIdentifier("git-rebase-onto-picker")
                }
            case .typed:
                TextField("Branch, tag or commit", text: $typed)
                    .focused($ontoFocused)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.done)
                    .disabled(busy)
                    .pixelField(isFocused: ontoFocused)
                    .accessibilityIdentifier("git-rebase-onto-field")
            }
        }
    }
}
