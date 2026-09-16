import SwiftUI

/// The New branch sheet (Android `GitNewBranchSheet`): a name, a start point
/// defaulting to the checked-out branch, and an opt-in checkout in the project
/// folder. Field text is local; the model only sees the submit. A `Toggle` is
/// fine here — the app-wide `.pixelCheckbox` style is only a problem inside a
/// toolbar `Menu`.
struct GitNewBranchSheet: View {
    @Bindable var model: GitScreenModel

    @Environment(\.mobaideColors) private var colors
    @FocusState private var nameFocused: Bool
    @State private var name = ""
    @State private var startPoint: String?
    @State private var checkout = false

    private var sheet: GitScreenModel.NewBranchSheetState { model.newBranch ?? .init() }

    private var busy: Bool { sheet.isCreating }

    private var submittable: Bool {
        !busy && !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var localItems: [GitItem] {
        model.visibleItems.filter { $0.branch != nil && $0.kind != GitItem.kindRemoteBranch }
    }

    private var remoteItems: [GitItem] {
        model.visibleItems.filter { $0.kind == GitItem.kindRemoteBranch }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    fields
                    Toggle(isOn: $checkout) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Check out in the project folder of \(model.projectName)")
                            Text("Refused while a session runs in that folder.")
                                .font(.footnote)
                                .foregroundStyle(colors.textMuted)
                        }
                    }
                    .disabled(busy)
                    .accessibilityIdentifier("git-new-branch-checkout")
                    if let error = sheet.error {
                        PixelErrorBanner(error)
                            .accessibilityIdentifier("git-new-branch-error")
                    }
                }
                .padding(16)
            }
            .pixelScreenBackground()
            .navigationTitle("New branch")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { model.closeNewBranch() }
                        .disabled(busy)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(busy ? "Creating…" : "Create") {
                        Task { await model.createBranch(name: name, startPoint: startPoint, checkout: checkout) }
                    }
                    .disabled(!submittable)
                    .accessibilityLabel("Create")
                    .accessibilityIdentifier("git-new-branch-submit")
                }
            }
        }
        .pixelSheet()
        .interactiveDismissDisabled(busy)
        .onAppear {
            startPoint = model.visibleItems.first(where: \.is_current)?.branch
        }
        .task {
            await Task.yield()
            nameFocused = true
        }
    }

    private var fields: some View {
        VStack(alignment: .leading, spacing: 12) {
            PixelLabeledField(label: "Name") {
                TextField("feature/login", text: $name)
                    .focused($nameFocused)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.done)
                    .disabled(busy)
                    .pixelField(isFocused: nameFocused)
                    .accessibilityIdentifier("git-new-branch-name")
            }
            PixelLabeledField(label: "Start point") {
                Picker("Start point", selection: $startPoint) {
                    Text("HEAD (current commit)").tag(String?.none)
                    ForEach(localItems, id: \.id) { item in
                        Text(GitItemDisplay.primaryLabel(item)).tag(Optional(item.branch ?? ""))
                    }
                    ForEach(remoteItems, id: \.id) { item in
                        Text("\(GitItemDisplay.primaryLabel(item)) (remote)").tag(Optional(item.branch ?? ""))
                    }
                }
                .labelsHidden()
                .disabled(busy)
                .accessibilityIdentifier("git-new-branch-start")
            }
        }
    }
}
