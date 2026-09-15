import SwiftUI

/// The commit sheet (Android `GitCommitSheet`): Summary + Description (sent as
/// `subject\n\nbody`), the author line from `commit/context`, Amend with the
/// previous message prefilled, and Skip hooks. Field text is local; the model
/// only sees the submit. A `Toggle` is fine here — the app-wide
/// `.pixelCheckbox` style is only a problem inside a toolbar `Menu`.
struct GitCommitSheet: View {
    @Bindable var model: GitChangesModel

    @Environment(\.mobaideColors) private var colors
    @FocusState private var summaryFocused: Bool
    @State private var summary = ""
    @State private var description = ""
    @State private var amend = false
    @State private var noVerify = false
    /// What Amend poured into the fields, so switching it off can take back
    /// exactly that and nothing the user typed since.
    @State private var prefill: (summary: String, description: String)?

    private var sheet: CommitSheetState { model.commitSheet ?? CommitSheetState() }

    private var ready: (author: GitCommitAuthor?, head: GitCommitHead?)? {
        if case .ready(let author, let head) = sheet.context { return (author, head) }
        return nil
    }

    private var busy: Bool { sheet.isCommitting }

    private var submittable: Bool {
        !busy
            && ready?.author != nil
            && !summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && (model.stagedCount > 0 || amend)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    contextSection
                    fields
                    toggles
                    if let error = sheet.error {
                        errorPanel(error)
                    }
                    if model.stagedCount == 0, !amend, ready != nil {
                        Text("Nothing is staged. Stage files first, or amend the previous commit.")
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                    }
                }
                .padding(16)
            }
            .pixelScreenBackground()
            .navigationTitle(amend ? "Amend commit" : "Commit")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { model.closeCommit() }
                        .disabled(busy)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(busy ? "Committing…" : GitChangesDisplay.commitButtonLabel(stagedCount: model.stagedCount, amend: amend)) {
                        Task { await model.commit(summary: summary, description: description, amend: amend, noVerify: noVerify) }
                    }
                    .disabled(!submittable)
                    .accessibilityLabel("Commit")
                }
            }
        }
        .pixelSheet()
        .interactiveDismissDisabled(busy)
        .task {
            await Task.yield()
            summaryFocused = true
        }
    }

    @ViewBuilder
    private var contextSection: some View {
        switch sheet.context {
        case .loading:
            LoadingIndicator()
        case .failed(let message):
            PixelErrorBanner(message)
        case .ready(let author, _):
            if let author {
                Text(GitChangesDisplay.authorLine(author))
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Git doesn't know who you are on the agent machine. Run these there, then try again:")
                        .font(.footnote)
                        .foregroundStyle(colors.danger)
                    Text(SessionGitCopy.identityFixCommands.joined(separator: "\n"))
                        .font(MobaideTypography.code)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .pixelCodePlate()
                }
                .pixelDangerPanel()
            }
        }
    }

    private var fields: some View {
        VStack(alignment: .leading, spacing: 12) {
            PixelLabeledField(label: "Summary") {
                TextField("What changed", text: $summary)
                    .focused($summaryFocused)
                    .submitLabel(.next)
                    .disabled(busy)
                    .pixelField(isFocused: summaryFocused)
                    .accessibilityIdentifier("git-commit-summary")
            }
            PixelLabeledField(label: "Description") {
                TextEditor(text: $description)
                    .font(MobaideTypography.code)
                    .foregroundStyle(colors.text)
                    .scrollContentBackground(.hidden)
                    .autocorrectionDisabled()
                    .frame(minHeight: 96)
                    .disabled(busy)
                    .pixelField()
                    .overlay(alignment: .topLeading) {
                        if description.isEmpty {
                            Text("Optional details")
                                .font(MobaideTypography.code)
                                .foregroundStyle(colors.gray)
                                .padding(.horizontal, 5)
                                .padding(.top, 8)
                                .allowsHitTesting(false)
                        }
                    }
                    .accessibilityIdentifier("git-commit-description")
            }
        }
    }

    private var toggles: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let head = ready?.head {
                Toggle(isOn: Binding(get: { amend }, set: { toggleAmend($0) })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Amend previous commit")
                        Text("Rewrites \(head.short_oid) · \(head.subject)")
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                }
                .disabled(busy)
                .accessibilityIdentifier("git-commit-amend")
            }
            Toggle("Skip hooks (--no-verify)", isOn: $noVerify)
                .disabled(busy)
                .accessibilityIdentifier("git-commit-no-verify")
        }
    }

    private func errorPanel(_ error: CommitError) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            PixelErrorBanner(error.message)
            if let output = error.output, !output.isEmpty {
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
    }

    private func toggleAmend(_ checked: Bool) {
        amend = checked
        guard let head = ready?.head else { return }
        if checked {
            if summary.trimmingCharacters(in: .whitespaces).isEmpty,
               description.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                prefill = (head.subject, head.body)
                summary = head.subject
                description = head.body
            }
            return
        }
        let poured = prefill
        prefill = nil
        if let poured, summary == poured.summary, description == poured.description {
            summary = ""
            description = ""
        }
    }
}
