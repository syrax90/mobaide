import SwiftUI

/// The Fetch / Pull / Push sheet (Android `GitSyncSheet`, web `GitSyncDialog`):
/// the head and upstream line, one row per action with its reason when it
/// cannot run, then — once a job exists — its progress with Cancel, or its
/// outcome with the follow-ups a failure allows. The sheet may be closed while
/// a job runs (the list's banner carries on), so only the start request itself
/// blocks dismissal. The follow-up confirmation is attached here, over the
/// sheet, because a dialog presented from the screen underneath would collide
/// with the sheet's own presentation.
struct GitSyncSheet: View {
    @Bindable var model: GitChangesModel

    @Environment(\.mobaideColors) private var colors

    private var sheet: SyncSheetState { model.syncSheet ?? SyncSheetState() }
    private var running: Bool { model.job?.running == true }
    private var locked: Bool { sheet.isStarting || running }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    summary
                    rows
                    if let error = sheet.error {
                        PixelErrorBanner(error)
                    }
                    if let banner = model.job {
                        if banner.running {
                            progress(banner)
                        } else {
                            result(banner)
                        }
                    }
                }
                .padding(16)
            }
            .pixelScreenBackground()
            .navigationTitle("Fetch / Pull / Push")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { model.closeSync() }
                        .disabled(sheet.isStarting)
                }
            }
        }
        .pixelSheet()
        .interactiveDismissDisabled(sheet.isStarting)
        .confirmationDialog(
            model.syncPrompt?.title ?? "Confirm?",
            isPresented: promptPresented,
            titleVisibility: .visible,
            presenting: model.syncPrompt
        ) { prompt in
            Button(prompt.confirm, role: prompt.followUp == .forcePush ? .destructive : nil) {
                Task { await model.confirmFollowUp() }
            }
            Button("Cancel", role: .cancel) { model.dismissFollowUp() }
        } message: { prompt in
            Text(prompt.body)
        }
    }

    // Only the prompt: SwiftUI writes this false before the confirm action
    // runs, and the follow-up it acts on must survive that write.
    private var promptPresented: Binding<Bool> {
        Binding(
            get: { model.syncPrompt != nil },
            set: { if !$0 { model.syncPrompt = nil } }
        )
    }

    private var summary: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(model.repo.map(GitChangesDisplay.headLabel) ?? "—")
                .font(.body.weight(.semibold))
                .foregroundStyle(colors.text)
                .lineLimit(1)
                .truncationMode(.middle)
            Text(GitChangesDisplay.upstreamLabel(model.repo?.upstream) ?? "No upstream")
                .font(.caption)
                .monospaced()
                .foregroundStyle(colors.textMuted)
                .lineLimit(1)
                .truncationMode(.middle)
        }
    }

    private var rows: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(GitChangesDisplay.syncRows(model.repo, capabilities: model.capabilities), id: \.op) { row in
                VStack(alignment: .leading, spacing: 4) {
                    Button(row.label) {
                        Task { await model.startSync(op: row.op) }
                    }
                    .buttonStyle(.pixel)
                    .disabled(!row.enabled || locked)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityIdentifier("git-sync-\(row.op)")
                    Text(row.detail)
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
            }
        }
    }

    private func progress(_ banner: GitJobBanner) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(banner.job.map { GitChangesDisplay.jobTitle($0.op) } ?? "Syncing…")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
                Spacer()
                if let job = banner.job, let phase = job.phase {
                    Text(job.percent.map { "\(phase) \($0)%" } ?? phase)
                        .font(.footnote)
                        .foregroundStyle(colors.textMuted)
                }
            }
            PixelProgressBar(percent: banner.job?.percent)
            if let line = banner.job?.line, !line.isEmpty {
                Text(line)
                    .font(MobaideTypography.code)
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(2)
                    .truncationMode(.tail)
            }
            Button("Cancel") {
                Task { await model.cancelSync() }
            }
            .buttonStyle(.pixelSmall)
            .accessibilityIdentifier("git-sync-cancel")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelCard()
        .accessibilityElement(children: .contain)
    }

    private func result(_ banner: GitJobBanner) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if banner.lost {
                PixelErrorBanner(GitChangesDisplay.jobLostCopy)
            } else if let job = banner.job {
                Text(GitChangesDisplay.jobDoneTitle(job.op, ok: job.ok == true))
                    .font(.body.weight(.semibold))
                    .foregroundStyle(job.ok == true ? colors.ok : colors.danger)
                if job.ok == true {
                    if let message = job.message, !message.isEmpty {
                        Text(message)
                            .font(MobaideTypography.code)
                            .foregroundStyle(colors.textMuted)
                    }
                } else {
                    Text(GitChangesDisplay.jobErrorCopy(job.error_code, message: job.message))
                        .font(.footnote)
                        .foregroundStyle(colors.text)
                    if GitChangesDisplay.showsJobOutput(job), let output = job.message {
                        ScrollView(.vertical) {
                            Text(output)
                                .font(MobaideTypography.code)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(maxHeight: 160)
                        .pixelCodePlate()
                    }
                    let followUps = GitChangesDisplay.jobFollowUps(job)
                    if !followUps.isEmpty {
                        HStack(spacing: 12) {
                            ForEach(followUps, id: \.self) { followUp in
                                Button(GitChangesDisplay.followUpLabel(followUp)) {
                                    model.requestFollowUp(followUp)
                                }
                                .buttonStyle(followUp == .forcePush ? .pixelSmallDanger : .pixelSmall)
                                .disabled(locked)
                            }
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .pixelCard()
    }
}
