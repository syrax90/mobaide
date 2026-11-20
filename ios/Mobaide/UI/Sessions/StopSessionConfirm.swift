import SwiftUI

extension SessionResponse {
    /// The branch of the worktree the agent made for this session, when it has one.
    var removableWorktreeBranch: String? {
        guard let branch = worktree_branch, !branch.isEmpty else { return nil }
        return branch
    }

    /// Another session works in this session's worktree, so the agent's busy
    /// guard would refuse removing it on stop.
    func worktreeInUse(among sessions: [SessionResponse]) -> Bool {
        guard let cwd else { return false }
        return sessions.contains { $0.session_id != session_id && $0.cwd == cwd }
    }
}

/// Stop / Force Stop confirmation. A session in an agent-made worktree also offers
/// removing the worktree and branch; a confirmation dialog cannot host a checkbox,
/// so it gets a small sheet. While another session shares the worktree it is disabled.
struct StopSessionConfirm: ViewModifier {
    @Binding var pending: SessionResponse?
    let sessions: [SessionResponse]
    let label: (SessionResponse) -> String
    let onStop: (_ session: SessionResponse, _ force: Bool, _ removeWorktree: Bool) -> Void

    static func worktreeRemovalLabel(_ branch: String) -> String {
        "Also remove the worktree and local branch “\(branch)”"
    }

    static let worktreeRemovalHint = "Uncommitted changes and commits that are only on this branch are lost."

    static let worktreeInUseHint = "Another session runs in this worktree. Stop it first to remove the worktree."

    private var dialogPresented: Binding<Bool> {
        Binding(
            get: { pending != nil && pending?.removableWorktreeBranch == nil },
            set: { if !$0 { pending = nil } }
        )
    }

    private var sheetTarget: Binding<WorktreeStopTarget?> {
        Binding(
            get: {
                guard let session = pending, let branch = session.removableWorktreeBranch else { return nil }
                return WorktreeStopTarget(session: session, branch: branch)
            },
            set: { if $0 == nil { pending = nil } }
        )
    }

    func body(content: Content) -> some View {
        content
            .confirmationDialog("Stop session?", isPresented: dialogPresented, presenting: pending) { session in
                Button("Stop", role: .destructive) { onStop(session, false, false) }
                Button("Force Stop", role: .destructive) { onStop(session, true, false) }
                Button("Cancel", role: .cancel) {}
            } message: { session in
                Text("Stop “\(label(session))”?")
            }
            .sheet(item: sheetTarget) { target in
                StopSessionSheet(
                    label: label(target.session),
                    branch: target.branch,
                    inUse: target.session.worktreeInUse(among: sessions)
                ) { force, removeWorktree in
                    pending = nil
                    onStop(target.session, force, removeWorktree)
                } onCancel: {
                    pending = nil
                }
            }
    }
}

extension View {
    func stopSessionConfirm(
        pending: Binding<SessionResponse?>,
        sessions: [SessionResponse],
        label: @escaping (SessionResponse) -> String,
        onStop: @escaping (_ session: SessionResponse, _ force: Bool, _ removeWorktree: Bool) -> Void
    ) -> some View {
        modifier(StopSessionConfirm(pending: pending, sessions: sessions, label: label, onStop: onStop))
    }
}

private struct WorktreeStopTarget: Identifiable {
    let session: SessionResponse
    let branch: String
    var id: String { session.session_id }
}

private struct StopSessionSheet: View {
    let label: String
    let branch: String
    let inUse: Bool
    let onStop: (_ force: Bool, _ removeWorktree: Bool) -> Void
    let onCancel: () -> Void

    @Environment(\.mobaideColors) private var colors
    // Never pre-checked: the removal throws away uncommitted and unmerged work.
    @State private var removeWorktree = false

    private var remove: Bool { !inUse && removeWorktree }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Stop session?")
                    .font(MobaideTypography.dialogTitle)
                    .foregroundStyle(colors.accent)
                Text("Stop “\(label)”? Force Stop kills the process immediately.")
                    .font(.callout)
                    .foregroundStyle(colors.textMuted)
                Toggle(isOn: Binding(get: { remove }, set: { removeWorktree = $0 })) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(StopSessionConfirm.worktreeRemovalLabel(branch))
                        Text(inUse ? StopSessionConfirm.worktreeInUseHint : StopSessionConfirm.worktreeRemovalHint)
                            .font(.footnote)
                            .foregroundStyle(colors.textMuted)
                    }
                }
                .disabled(inUse)
                .accessibilityIdentifier("stop-session-remove-worktree")
                VStack(spacing: 8) {
                    Button("Stop") { onStop(false, remove) }
                        .buttonStyle(.pixelDangerBlock)
                        .accessibilityIdentifier("stop-session-stop")
                    Button("Force Stop") { onStop(true, remove) }
                        .buttonStyle(.pixelDangerBlock)
                        .accessibilityIdentifier("stop-session-force")
                    Button("Cancel", role: .cancel) { onCancel() }
                        .buttonStyle(.pixelBlock)
                        .accessibilityIdentifier("stop-session-cancel")
                }
                .padding(.top, 4)
            }
            .padding(20)
        }
        .pixelSheet()
        .presentationDetents([.medium])
    }
}
