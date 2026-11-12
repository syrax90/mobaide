import SwiftUI
import UIKit

/// The oldest pending request, raised above the composer: a permission prompt
/// with the agent's options, or a form question. Never modal — the timeline
/// stays scrollable behind it. Only the body scrolls, up to `bodyCap`; the
/// answer buttons stay in view.
struct ChatRequestCard: View {
    let model: ChatSessionModel
    let request: ChatPendingRequest
    let moreWaiting: Int
    let bodyCap: CGFloat

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            switch request {
            case .permission(let permission):
                ChatPermissionBody(model: model, request: permission, bodyCap: bodyCap)
            case .form(let form):
                ChatFormBody(model: model, request: form, bodyCap: bodyCap)
                    .id(form.id)
            case .signIn(let signIn):
                // A new stage starts with an empty field.
                ChatSignInBody(model: model, request: signIn, bodyCap: bodyCap)
                    .id("\(signIn.id):\(signIn.stage)")
            case .other(_, _, let title):
                Text(title)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(colors.text)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(colors.bg2)
        .pixelRing(MobaideColorScheme.brand2)
        .pixelShadow(MobaideColorScheme.btnShadow)
        .accessibilityElement(children: .contain)
    }

    private var header: some View {
        HStack(spacing: 8) {
            Image(systemName: headerIcon)
                .foregroundStyle(colors.accent)
            Text(headerTitle)
                .chromeText()
                .foregroundStyle(colors.accent)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 4)
            if moreWaiting > 0 {
                Text("+\(moreWaiting) more waiting")
                    .font(.caption)
                    .foregroundStyle(colors.textMuted)
            }
        }
    }

    private var headerIcon: String {
        switch request {
        case .permission(let permission): return ChatToolStyle.icon(permission.toolKind)
        case .form: return "questionmark.bubble"
        case .signIn: return "person.badge.key"
        case .other: return "hand.raised"
        }
    }

    private var headerTitle: String {
        switch request {
        case .permission: return "Permission needed"
        case .form: return "Question"
        case .signIn: return "Sign-in needed"
        case .other: return "Waiting for you"
        }
    }
}

private struct ChatPermissionBody: View {
    let model: ChatSessionModel
    let request: ChatPendingRequest.Permission
    let bodyCap: CGFloat

    @Environment(\.mobaideColors) private var colors

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ChatCappedScroll(maxHeight: bodyCap, fadeColor: colors.bg2) {
                VStack(alignment: .leading, spacing: 10) {
                    Text(request.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(colors.text)
                        .fixedSize(horizontal: false, vertical: true)
                    if let subtitle = request.subtitle, !subtitle.isEmpty, subtitle != request.title {
                        Text(subtitle)
                            .font(.footnote.monospaced())
                            .foregroundStyle(colors.textMuted)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !request.content.isEmpty {
                        preview
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            VStack(spacing: 10) {
                ForEach(Array(request.options.enumerated()), id: \.element.id) { index, option in
                    optionButton(option, primary: index == firstAllowOnceIndex)
                }
            }
        }
    }

    private var preview: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(request.content.enumerated()), id: \.offset) { index, part in
                ChatContentPartView(part: part, cacheKey: "\(request.id)#\(index)")
            }
        }
    }

    private var firstAllowOnceIndex: Int? {
        request.options.firstIndex { $0.kind == "allow_once" }
    }

    private func optionButton(_ option: ChatPermissionOption, primary: Bool) -> some View {
        let answering = model.answering[request.id]
        let busy = answering == option.id
        return Button {
            UISelectionFeedbackGenerator().selectionChanged()
            model.respond(to: request, option: option)
        } label: {
            HStack(spacing: 8) {
                if busy {
                    PixelLoadingBlock(width: 7, height: 13)
                }
                Text(option.name)
                    .multilineTextAlignment(.center)
            }
        }
        .buttonStyle(style(option, primary: primary))
        .disabled(!model.isLive || (answering != nil && !busy))
        .allowsHitTesting(answering == nil)
    }

    private func style(_ option: ChatPermissionOption, primary: Bool) -> PixelButtonStyle {
        if option.isReject { return .pixelDangerGhostBlock }
        return primary ? .pixelPrimaryBlock : .pixelBlock
    }
}

/// A form answer under edit.
enum ChatFormValue: Equatable {
    case text(String)
    case flag(Bool)
    case choice(String?)
    case choices(Set<String>)

    static func initial(_ field: ChatFormField) -> ChatFormValue {
        switch field.type {
        case "boolean":
            return .flag(field.defaultValue?.boolValue ?? false)
        case "select":
            let value = field.defaultValue?.stringValue
            return .choice(field.options.contains { $0.value == value } ? value : nil)
        case "multiselect":
            let values = field.defaultValue?.arrayValue?.compactMap(\.stringValue) ?? []
            return .choices(Set(values))
        case "number", "integer":
            switch field.defaultValue {
            case .int(let value)?: return .text(String(value))
            case .double(let value)?: return .text(value.rounded() == value ? String(Int(value)) : String(value))
            default: return .text("")
            }
        default:
            return .text(field.defaultValue?.stringValue ?? "")
        }
    }

    /// The wire value, or nil when the field is left empty.
    func wire(for field: ChatFormField) -> JSONValue? {
        switch (self, field.type) {
        case (.flag(let flag), _):
            return .bool(flag)
        case (.choice(let value), _):
            return value.map { .string($0) }
        case (.choices(let values), _):
            guard !values.isEmpty else { return nil }
            let ordered = field.options.map(\.value).filter(values.contains)
            return .array(ordered.map { .string($0) })
        case (.text(let raw), "integer"):
            return Int(raw.trimmingCharacters(in: .whitespaces)).map { .int($0) }
        case (.text(let raw), "number"):
            let trimmed = raw.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")
            return Double(trimmed).map { .double($0) }
        case (.text(let raw), _):
            return raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : .string(raw)
        }
    }

    /// Typed text that is not a number is never valid, required or not.
    func isValid(for field: ChatFormField) -> Bool {
        if case .text(let raw) = self, field.type == "number" || field.type == "integer",
           !raw.trimmingCharacters(in: .whitespaces).isEmpty {
            return wire(for: field) != nil
        }
        return !field.required || wire(for: field) != nil
    }
}

private struct ChatFormBody: View {
    let model: ChatSessionModel
    let request: ChatPendingRequest.Form
    let bodyCap: CGFloat

    @Environment(\.mobaideColors) private var colors
    @State private var values: [String: ChatFormValue] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ChatCappedScroll(maxHeight: bodyCap, fadeColor: colors.bg2) {
                VStack(alignment: .leading, spacing: 12) {
                    Text(request.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(colors.text)
                        .fixedSize(horizontal: false, vertical: true)
                    ForEach(request.fields) { field in
                        fieldView(field)
                    }
                    if let error = model.formErrors[request.id] {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(colors.danger)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            buttons
        }
        .onAppear {
            if values.isEmpty {
                values = Dictionary(uniqueKeysWithValues: request.fields.map { ($0.key, ChatFormValue.initial($0)) })
            }
        }
    }

    private func value(_ field: ChatFormField) -> ChatFormValue {
        values[field.key] ?? .initial(field)
    }

    private var canSubmit: Bool {
        request.fields.allSatisfy { value($0).isValid(for: $0) }
    }

    @ViewBuilder
    private func fieldView(_ field: ChatFormField) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if field.type != "boolean" {
                label(field)
            }
            switch field.type {
            case "select":
                ForEach(field.options) { option in
                    choiceRow(field, option, multi: false)
                }
            case "multiselect":
                ForEach(field.options) { option in
                    choiceRow(field, option, multi: true)
                }
            case "boolean":
                Toggle(isOn: Binding(
                    get: { if case .flag(let flag) = value(field) { return flag } else { return false } },
                    set: { values[field.key] = .flag($0) }
                )) {
                    VStack(alignment: .leading, spacing: 2) {
                        label(field)
                        if let description = field.description {
                            Text(description).font(.caption).foregroundStyle(colors.textMuted)
                        }
                    }
                }
                .frame(minHeight: 44)
            default:
                TextField(field.description ?? "", text: Binding(
                    get: { if case .text(let text) = value(field) { return text } else { return "" } },
                    set: { values[field.key] = .text($0) }
                ))
                .keyboardType(field.type == "integer" ? .numberPad : field.type == "number" ? .decimalPad : .default)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .pixelField(isInvalid: !value(field).isValid(for: field) && !isEmptyText(value(field)))
                .accessibilityLabel(field.title)
                if field.type != "text", let description = field.description {
                    Text(description).font(.caption).foregroundStyle(colors.textMuted)
                }
            }
        }
    }

    private func isEmptyText(_ value: ChatFormValue) -> Bool {
        if case .text(let text) = value { return text.trimmingCharacters(in: .whitespaces).isEmpty }
        return false
    }

    private func label(_ field: ChatFormField) -> some View {
        HStack(spacing: 4) {
            Text(field.title)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(colors.text)
            if field.required {
                Text("Required")
                    .font(.caption2)
                    .foregroundStyle(colors.accent)
            }
        }
    }

    private func choiceRow(_ field: ChatFormField, _ option: ChatFormChoice, multi: Bool) -> some View {
        let selected: Bool = {
            switch value(field) {
            case .choice(let current): return current == option.value
            case .choices(let set): return set.contains(option.value)
            default: return false
            }
        }()
        return Button {
            if multi {
                var set: Set<String> = []
                if case .choices(let current) = value(field) { set = current }
                if set.remove(option.value) == nil { set.insert(option.value) }
                values[field.key] = .choices(set)
            } else {
                values[field.key] = .choice(option.value)
            }
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Group {
                    if multi {
                        PixelCheckbox(isOn: selected)
                    } else {
                        PixelRadio(isSelected: selected)
                    }
                }
                .padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text(option.label)
                        .foregroundStyle(colors.text)
                    if let description = option.description {
                        Text(description)
                            .font(.caption)
                            .foregroundStyle(colors.textMuted)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var buttons: some View {
        let answering = model.answering[request.id]
        return VStack(spacing: 10) {
            Button {
                UISelectionFeedbackGenerator().selectionChanged()
                var wire: [String: JSONValue] = [:]
                for field in request.fields {
                    if let value = value(field).wire(for: field) {
                        wire[field.key] = value
                    }
                }
                model.respond(to: request, action: "accept", values: wire)
            } label: {
                HStack(spacing: 8) {
                    if answering == "accept" { PixelLoadingBlock(width: 7, height: 13) }
                    Text("Submit")
                }
            }
            .buttonStyle(.pixelPrimaryBlock)
            .disabled(!canSubmit || !model.isLive || answering != nil)
            HStack(spacing: 10) {
                Button {
                    model.respond(to: request, action: "decline", values: nil)
                } label: {
                    HStack(spacing: 8) {
                        if answering == "decline" { PixelLoadingBlock(width: 7, height: 13) }
                        Text("Decline")
                    }
                }
                .buttonStyle(.pixelBlock)
                .disabled(!model.isLive || answering != nil)
                Button("Cancel") {
                    model.respond(to: request, action: "cancel", values: nil)
                }
                .font(MobaideTypography.bodyLarge)
                .foregroundStyle(colors.accent)
                .frame(minWidth: 88, minHeight: 44)
                .disabled(!model.isLive || answering != nil)
            }
        }
    }
}

/// Sign-in to the coding agent on the PC: pick a method, then open the
/// sign-in page, enter the one-time code shown here on it, or paste the code
/// it shows.
private struct ChatSignInBody: View {
    let model: ChatSessionModel
    let request: ChatPendingRequest.SignIn
    let bodyCap: CGFloat

    @Environment(\.mobaideColors) private var colors
    @Environment(\.openURL) private var openURL
    @State private var value = ""
    @State private var revealed = false
    @FocusState private var fieldFocused: Bool

    private var answering: String? { model.answering[request.id] }
    private var input: ChatPendingRequest.SignIn.Input? { request.waiting ? request.input : nil }
    private var canSubmit: Bool {
        model.isLive && answering == nil && !request.busy
            && !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ChatCappedScroll(maxHeight: bodyCap, fadeColor: colors.bg2) {
                VStack(alignment: .leading, spacing: 10) {
                    Text(request.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(colors.text)
                        .fixedSize(horizontal: false, vertical: true)
                    if !request.text.isEmpty {
                        Text(request.text)
                            .foregroundStyle(colors.text)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if !request.waiting, request.methods.count > 1 {
                        ForEach(request.methods, id: \.id) { method in
                            methodLine(method)
                        }
                    }
                    if input != nil {
                        steps
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            // A sign-in with nothing to type (a one-time code to enter on the
            // page) keeps its page button and code in view, outside the
            // capped scroll. One with a field scrolls, so the keyboard fits.
            if input == nil {
                steps
            }
            buttons
        }
    }

    @ViewBuilder
    private var steps: some View {
        if request.waiting, let url = request.url {
            Button {
                openURL(url)
            } label: {
                Label("Open sign-in page", systemImage: "arrow.up.right.square")
            }
            .buttonStyle(.pixelPrimaryBlock)
            .disabled(!model.isLive)
        }
        if request.waiting, let code = request.userCode {
            CopyableCodeBlock(label: "One-time code", code: code)
        }
        if let input {
            field(input)
        }
        if request.waiting, request.busy, input == nil {
            HStack(spacing: 8) {
                PixelLoadingBlock(width: 7, height: 13)
                Text(request.url == nil ? "Starting the sign-in…" : "Waiting for you to finish on the sign-in page…")
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
        }
        if let error = model.formErrors[request.id] ?? request.error, !error.isEmpty {
            Text(error)
                .font(.footnote)
                .foregroundStyle(colors.danger)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func methodLine(_ method: ChatPendingRequest.SignIn.Method) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(method.name)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(colors.text)
            if !method.description.isEmpty {
                Text(method.description)
                    .font(.footnote)
                    .foregroundStyle(colors.textMuted)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    @ViewBuilder
    private func field(_ input: ChatPendingRequest.SignIn.Input) -> some View {
        let label = input.label.isEmpty ? "Code" : input.label
        let enabled = model.isLive && answering == nil && !request.busy
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.subheadline.weight(.medium))
                .foregroundStyle(colors.text)
            if input.secret {
                SecretTextField(text: $value, label: label, revealed: $revealed, enabled: enabled, isFocused: fieldFocused)
                    .focused($fieldFocused)
                    .onSubmit(submit)
            } else {
                TextField(label, text: $value)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.done)
                    .focused($fieldFocused)
                    .onSubmit(submit)
                    .pixelField(isFocused: fieldFocused)
                    .disabled(!enabled)
                    .accessibilityLabel(label)
            }
        }
    }

    private func submit() {
        guard canSubmit else { return }
        UISelectionFeedbackGenerator().selectionChanged()
        model.submitSignIn(request, value: value)
    }

    private var buttons: some View {
        VStack(spacing: 10) {
            if !request.waiting {
                ForEach(Array(request.methods.enumerated()), id: \.element.id) { index, method in
                    methodButton(method, primary: index == 0)
                }
            }
            if input != nil {
                let sending = request.busy || answering == ChatSessionModel.signInSubmitKey
                Button(action: submit) {
                    HStack(spacing: 8) {
                        if sending { PixelLoadingBlock(width: 7, height: 13) }
                        Text("Submit")
                    }
                }
                .buttonStyle(.pixelPrimaryBlock)
                .disabled(!canSubmit && !sending)
                .allowsHitTesting(canSubmit)
            }
            Button(request.waiting ? "Cancel" : "Not now") {
                model.cancelSignIn(request)
            }
            .font(MobaideTypography.bodyLarge)
            .foregroundStyle(colors.accent)
            .frame(minWidth: 88, minHeight: 44)
            .disabled(!model.isLive || answering != nil)
        }
    }

    private func methodButton(_ method: ChatPendingRequest.SignIn.Method, primary: Bool) -> some View {
        let key = ChatSessionModel.signInStartKey(method.id)
        return Button {
            UISelectionFeedbackGenerator().selectionChanged()
            model.startSignIn(request, method: method)
        } label: {
            HStack(spacing: 8) {
                if answering == key { PixelLoadingBlock(width: 7, height: 13) }
                Text(request.methods.count == 1 ? "Sign in" : method.name)
            }
        }
        .buttonStyle(primary ? .pixelPrimaryBlock : .pixelBlock)
        .disabled(!model.isLive || (answering != nil && answering != key))
        .allowsHitTesting(answering == nil)
    }
}
