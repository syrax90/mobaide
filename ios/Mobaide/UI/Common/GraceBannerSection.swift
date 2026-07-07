import Observation
import SwiftUI

/// Mirror of Android `GraceBannerSection`: drop-in banner shown while the
/// account is inside the soft-delete grace window, with a confirm dialog
/// to cancel the scheduled deletion. Renders nothing when no deletion is
/// pending.
struct GraceBannerSection: View {
    @State private var model: GraceBannerModel

    @Environment(\.mobaideColors) private var colors

    init(authRepository: AuthRepository) {
        _model = State(initialValue: GraceBannerModel(authRepository: authRepository))
    }

    var body: some View {
        if let scheduledFor = model.scheduledFor {
            VStack(alignment: .leading, spacing: 8) {
                Label("Account scheduled for deletion", systemImage: "exclamationmark.triangle.fill")
                    .font(.subheadline.weight(.semibold))
                Text("Final purge on \(scheduledFor). Cancel any time before then to keep your account.")
                    .font(.footnote)
                if let errorMessage = model.errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(colors.error)
                }
                Button {
                    model.showConfirm = true
                } label: {
                    if model.cancelling {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Text("Cancel scheduled deletion")
                    }
                }
                .buttonStyle(.bordered)
                .disabled(model.cancelling)
            }
            .foregroundStyle(colors.onErrorContainer)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(colors.errorContainer, in: RoundedRectangle(cornerRadius: 8))
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .alert("Keep your account active?", isPresented: $model.showConfirm) {
                Button("Keep account active") {
                    Task { await model.confirmCancel() }
                }
                Button("Dismiss", role: .cancel) {}
            } message: {
                Text("This cancels the scheduled deletion and keeps your account.")
            }
        }
    }
}

@MainActor
@Observable
final class GraceBannerModel {
    private let authRepository: AuthRepository

    private(set) var scheduledFor: String?
    private(set) var cancelling = false
    private(set) var errorMessage: String?
    var showConfirm = false

    init(authRepository: AuthRepository) {
        self.authRepository = authRepository
        self.scheduledFor = authRepository.getScheduledDeletionAt()
    }

    func confirmCancel() async {
        cancelling = true
        errorMessage = nil
        do {
            _ = try await authRepository.cancelAccountDeletion()
            scheduledFor = nil
        } catch {
            errorMessage = "Could not cancel the scheduled deletion. Check your connection and try again."
        }
        cancelling = false
        showConfirm = false
    }
}
