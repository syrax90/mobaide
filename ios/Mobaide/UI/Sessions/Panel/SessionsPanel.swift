import SwiftUI

/// The terminal's left drawer (Herdr's sidebar): the connected agent's
/// sessions for one-tap switching or stopping, a shortcut to the create flow, and the
/// account's other agents. Stateless — the drawer host owns open/closed.
///
/// `departingSessionId` is a session already stopped from this panel whose row
/// may still be listed until the agent's push drops it: it is dimmed and inert
/// meanwhile, as is the row of a stop still in flight.
struct SessionsPanel: View {
    let model: SessionsPanelModel
    let currentSessionId: String
    var departingSessionId: String?
    /// The agent a drawer-started switch is connecting to, if any.
    let switchingAgentId: String?
    let onSelectSession: (String) -> Void
    let onStopSession: (SessionResponse) -> Void
    let onCreateSession: () -> Void
    let onSelectAgent: (AgentSummaryResponse) -> Void
    let onClose: () -> Void

    @Environment(\.mobaideColors) private var colors

    /// How long the selection frame takes to move, and a stopped row to fade out.
    static let selectionAnimation = Animation.easeInOut(duration: 0.25)

    var body: some View {
        VStack(spacing: 0) {
            header
            PixelDivider()
            agentLine
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        if !model.synced {
                            note("Reconnecting…")
                        } else if model.sessions.isEmpty {
                            note("No active sessions")
                        }
                        ForEach(model.sessions, id: \.session_id) { session in
                            let selected = session.session_id == currentSessionId
                            let departing = session.session_id == departingSessionId
                                || session.session_id == model.stoppingSessionId
                            Button {
                                if selected { onClose() } else { onSelectSession(session.session_id) }
                            } label: {
                                SessionsPanelRow(
                                    session: session,
                                    kindLabel: SessionDisplay.kindLabel(session, customTypeNames: model.customTypeNames),
                                    selected: selected,
                                    stopping: model.stoppingSessionId != nil || session.session_id == departingSessionId,
                                    onStop: { onStopSession(session) }
                                )
                                .opacity(departing ? 0.4 : 1)
                            }
                            .buttonStyle(.plain)
                            .disabled(departing)
                            .accessibilityAddTraits(selected ? .isSelected : [])
                            .accessibilityIdentifier("sessions-panel-row-\(session.session_id)")
                            .transition(.opacity)
                        }
                        Button(action: onCreateSession) {
                            HStack(spacing: 8) {
                                Image(systemName: "plus")
                                Text("New session")
                            }
                            .font(MobaideTypography.chromeBody)
                            .foregroundStyle(colors.accent)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .padding(.horizontal, 4)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityIdentifier("sessions-panel-new-session")

                        let others = model.otherAgents
                        if !others.isEmpty {
                            PixelSectionHeader("Agents")
                                .padding(.top, 8)
                                .padding(.horizontal, 4)
                            ForEach(others, id: \.agent_id) { agent in
                                let switching = switchingAgentId == agent.agent_id
                                Button {
                                    onSelectAgent(agent)
                                } label: {
                                    agentRow(agent, switching: switching)
                                }
                                .buttonStyle(.plain)
                                .disabled(!agent.online || switchingAgentId != nil)
                                .accessibilityIdentifier("sessions-panel-agent-\(agent.agent_id)")
                            }
                        }
                    }
                    .padding(EdgeInsets(top: 4, leading: 12, bottom: 16, trailing: 12))
                    // A stopped row fades out and its neighbours close the gap; the
                    // selection frame and dimming follow the same curve.
                    .animation(Self.selectionAnimation, value: model.sessions.map(\.session_id))
                    .animation(Self.selectionAnimation, value: currentSessionId)
                    .animation(Self.selectionAnimation, value: departingSessionId)
                    .animation(Self.selectionAnimation, value: model.stoppingSessionId)
                }
                // The selection can move without a tap (a stop hands it to the
                // nearest session), so keep the current row in view.
                .onChange(of: currentSessionId) { _, id in
                    withAnimation(Self.selectionAnimation) { proxy.scrollTo(id) }
                }
            }
        }
        .accessibilityIdentifier("sessions-panel")
    }

    private var header: some View {
        HStack {
            Text("Sessions")
                .font(MobaideTypography.dialogTitle)
                .foregroundStyle(colors.accent)
            Spacer()
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(colors.text)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close sessions panel")
            .accessibilityIdentifier("sessions-panel-close")
        }
        .padding(.leading, 16)
        .padding(.trailing, 4)
        .frame(minHeight: 52)
    }

    private var agentLine: some View {
        let agent = model.connectedAgent
        let online = agent?.online == true
        return HStack(spacing: 8) {
            PixelStatusSquare(tone: online ? .ok : .muted)
            Text(agent?.name ?? "Connected agent")
                .font(.body.weight(.semibold))
                .foregroundStyle(colors.text)
                .lineLimit(1)
            Spacer()
            Text(online ? "Online" : "Offline")
                .chromeText()
                .foregroundStyle(online ? colors.ok : colors.textMuted)
        }
        .padding(EdgeInsets(top: 12, leading: 16, bottom: 12, trailing: 16))
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(colors.textMuted)
            .padding(EdgeInsets(top: 12, leading: 4, bottom: 12, trailing: 4))
    }

    private func agentRow(_ agent: AgentSummaryResponse, switching: Bool) -> some View {
        HStack(spacing: 10) {
            PixelStatusSquare(tone: agent.online ? .ok : .muted)
            Text(agent.name)
                .font(.body.weight(.semibold))
                .foregroundStyle(agent.online ? colors.text : colors.textMuted)
                .lineLimit(1)
            Spacer()
            Text(switching ? "Connecting…" : agent.online ? "Online" : "Offline")
                .chromeText()
                .foregroundStyle(agent.online ? colors.ok : colors.textMuted)
        }
        .frame(minHeight: 44)
        .padding(.horizontal, 4)
        .contentShape(Rectangle())
    }
}

/// One session row: status square, title, `kind · project`, attention chip,
/// and — as on the sessions list's card — a stop control nested in the row's
/// tap target, which a plain-styled outer button leaves independently tappable.
private struct SessionsPanelRow: View {
    let session: SessionResponse
    let kindLabel: String
    let selected: Bool
    let stopping: Bool
    let onStop: () -> Void

    @Environment(\.mobaideColors) private var colors

    private var title: String {
        if let title = session.title, !title.isBlank { return title }
        return "\(kindLabel) · \(session.project_name ?? String(session.session_id.prefix(8)))"
    }

    var body: some View {
        HStack(spacing: 10) {
            SessionStatusIndicator(session: session)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
                    .lineLimit(1)
                Text([kindLabel, session.project_name].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            if let attention = SessionDisplay.statusChip(session) {
                PixelChip(attention.text, .status(attention.tone))
                    .fixedSize()
            }
            if SessionDisplay.canStop(session.state) {
                Button(action: onStop) {
                    Image(systemName: "stop.fill")
                        .foregroundStyle(stopping ? colors.textMuted : colors.danger)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(stopping)
                .accessibilityLabel("Stop session")
                .accessibilityIdentifier("sessions-panel-stop-\(session.session_id)")
            }
        }
        .contentShape(Rectangle())
        .pixelCard(selected: selected, insets: EdgeInsets(top: 10, leading: 12, bottom: 10, trailing: 12))
    }
}
