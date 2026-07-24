import Foundation
import Observation

/// Which sheet the projects list is presenting.
enum ProjectFormMode: Identifiable {
    case create
    case edit(ProjectInfo)

    var id: String {
        switch self {
        case .create: return "create"
        case .edit(let project): return "edit:\(project.name)"
        }
    }
}

/// A `default_flags` entry backing one editor row. The wire value is
/// heterogeneous (str | bool | number), typed here so the UI picks the right
/// input control (Android `FlagEntry`).
struct FlagEntry: Identifiable, Equatable {
    enum Kind: String, CaseIterable, Identifiable {
        case string
        case boolean
        case number

        var id: String { rawValue }
        var label: String {
            switch self {
            case .string: return "String"
            case .boolean: return "Boolean"
            case .number: return "Number"
            }
        }
    }

    let id: UUID
    var key: String
    var kind: Kind
    var textValue: String

    init(id: UUID = UUID(), key: String = "", kind: Kind = .string, textValue: String = "") {
        self.id = id
        self.key = key
        self.kind = kind
        self.textValue = textValue
    }

    var keyValid: Bool { !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var valueValid: Bool {
        switch kind {
        case .string: return true
        case .boolean: return textValue == "true" || textValue == "false"
        case .number: return Int(textValue) != nil || Double(textValue) != nil
        }
    }

    var signature: String { "\(key)\u{1}\(kind.rawValue)\u{1}\(textValue)" }

    /// Wire encoding (Android `toJsonElement`): number prefers an integer, then
    /// a double, defaulting to 0 for an unparseable value.
    func toJSONValue() -> JSONValue {
        switch kind {
        case .string: return .string(textValue)
        case .boolean: return .bool(textValue == "true")
        case .number:
            if let intValue = Int(textValue) { return .int(intValue) }
            return .double(Double(textValue) ?? 0)
        }
    }

    /// Edit seed (Android `fromJsonElement`): a JSON string stays STRING even
    /// when it reads `"true"`; then bool, integer, double, else STRING.
    static func from(key: String, value: JSONValue) -> FlagEntry {
        switch value {
        case .string(let text): return FlagEntry(key: key, kind: .string, textValue: text)
        case .bool(let flag): return FlagEntry(key: key, kind: .boolean, textValue: flag ? "true" : "false")
        case .int(let number): return FlagEntry(key: key, kind: .number, textValue: String(number))
        case .double(let number): return FlagEntry(key: key, kind: .number, textValue: Self.numberText(number))
        default: return FlagEntry(key: key, kind: .string, textValue: "\(value)")
        }
    }

    private static func numberText(_ value: Double) -> String {
        // `String(Int(value))` traps for magnitudes beyond Int range; a value
        // that large arrives as `.double` (JSONValue's Int decode overflows).
        value == value.rounded() ? String(format: "%.0f", value) : String(value)
    }
}

/// Create/edit project form state + validation (Android `ProjectFormState`).
/// The three-state `PUT` body is built in `toUpdateBody`: a field equal to the
/// edited project is omitted, an emptied string clears it (`""`), any other
/// value replaces it.
@MainActor
@Observable
final class ProjectFormModel {
    var name = ""
    var path = ""
    var condaEnv = ""
    var linearApiKey = ""
    var clearLinearKey = false
    var flags: [FlagEntry] = []

    var triggerLabels: Set<String> = ["AutoPipeline"]
    var triggerStates: Set<String> = ["Proposals"]
    var activeState = "In Progress"
    var doneState = "Done"
    var removalLabel = "AutoPipeline"

    private(set) var discoveredLabels: [LinearLabelDto] = []
    private(set) var discoveredStates: [LinearStateDto] = []
    private(set) var metadataLoading = false
    private(set) var metadataError: String?

    private(set) var submitting = false
    var submitAttempted = false
    var submitError: String?
    var nameServerError: String?
    var message: String?

    /// Set to the returned `ProjectInfo` after a successful create so the
    /// presenting screen can select the just-created project (New Session flow).
    private(set) var createdProject: ProjectInfo?

    let editing: ProjectInfo?
    private let repository: ProjectsRepository

    private let seededName: String
    private let seededPath: String
    private let seededConda: String
    private let seededFlagSignatures: [String]
    private let seededLabels: Set<String>
    private let seededStates: Set<String>
    private let seededActiveState: String
    private let seededDoneState: String
    private let seededRemovalLabel: String

    init(mode: ProjectFormMode, repository: ProjectsRepository) {
        self.repository = repository

        let initialName: String
        let initialPath: String
        let initialConda: String
        let initialFlags: [FlagEntry]
        let initialLabels: Set<String>
        let initialStates: Set<String>
        let initialActiveState: String
        let initialDoneState: String
        let initialRemovalLabel: String

        switch mode {
        case .create:
            editing = nil
            initialName = ""
            initialPath = ""
            initialConda = ""
            initialFlags = []
            initialLabels = ["AutoPipeline"]
            initialStates = ["Proposals"]
            initialActiveState = "In Progress"
            initialDoneState = "Done"
            initialRemovalLabel = "AutoPipeline"
        case .edit(let project):
            editing = project
            initialName = project.name
            initialPath = project.path
            initialConda = project.conda_env ?? ""
            initialFlags = project.default_flags
                .sorted { $0.key < $1.key }
                .map { FlagEntry.from(key: $0.key, value: $0.value) }
            let filter = project.auto_scan_filter
            initialLabels = Set(filter.label_names)
            initialStates = Set(filter.state_names)
            initialActiveState = filter.active_state
            initialDoneState = filter.done_state
            initialRemovalLabel = filter.removal_label
        }

        name = initialName
        path = initialPath
        condaEnv = initialConda
        flags = initialFlags
        triggerLabels = initialLabels
        triggerStates = initialStates
        activeState = initialActiveState
        doneState = initialDoneState
        removalLabel = initialRemovalLabel

        seededName = initialName
        seededPath = initialPath
        seededConda = initialConda
        seededFlagSignatures = initialFlags.map(\.signature)
        seededLabels = initialLabels
        seededStates = initialStates
        seededActiveState = initialActiveState
        seededDoneState = initialDoneState
        seededRemovalLabel = initialRemovalLabel
    }

    var isEditing: Bool { editing != nil }
    var title: String { isEditing ? "Edit project" : "New project" }

    // MARK: - Chip option lists (union of discovered + selected, order-stable)

    var labelOptions: [String] {
        orderedUnion(discoveredLabels.map(\.name), Array(triggerLabels), [removalLabel])
    }

    var stateOptions: [LinearStateDto] {
        var seen = Set<String>()
        var result: [LinearStateDto] = []
        let extras = triggerStates.map { LinearStateDto(name: $0) }
            + [activeState, doneState].map { LinearStateDto(name: $0) }
        for state in discoveredStates + extras where !state.name.isEmpty {
            if seen.insert(state.name).inserted { result.append(state) }
        }
        return result
    }

    private func orderedUnion(_ groups: [String]...) -> [String] {
        var seen = Set<String>()
        var result: [String] = []
        for group in groups {
            for value in group where !value.isEmpty {
                if seen.insert(value).inserted { result.append(value) }
            }
        }
        return result
    }

    // MARK: - Dirty tracking

    var flagsDirty: Bool { flags.map(\.signature) != seededFlagSignatures }

    var filterDirty: Bool {
        triggerLabels != seededLabels
            || triggerStates != seededStates
            || activeState != seededActiveState
            || doneState != seededDoneState
            || removalLabel != seededRemovalLabel
    }

    var isDirty: Bool {
        name != seededName || path != seededPath || condaEnv != seededConda
            || !linearApiKey.isEmpty || clearLinearKey || flagsDirty || filterDirty
    }

    /// The auto-scan filter section only matters once the project has a Linear
    /// key (existing or newly typed), matching Android `showAutoScanFilterSection`.
    var showsFilterSection: Bool {
        !linearApiKey.trimmingCharacters(in: .whitespaces).isEmpty
            || (!clearLinearKey && editing?.has_linear_key == true)
    }

    // MARK: - Validation

    var nameError: String? {
        if let nameServerError { return nameServerError }
        if name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Name is required" }
        if !matchesValidName(name) { return "Use letters, digits, spaces, _-." }
        return nil
    }

    var pathError: String? {
        path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Path is required" : nil
    }

    func flagKeyError(at index: Int) -> String? {
        let flag = flags[index]
        if !flag.keyValid { return "Key required" }
        if flags[..<index].contains(where: { $0.key == flag.key }) { return "Duplicate key" }
        return nil
    }

    func flagValueError(at index: Int) -> String? {
        let flag = flags[index]
        if flag.valueValid { return nil }
        switch flag.kind {
        case .boolean: return "Expected true/false"
        case .number: return "Not a number"
        case .string: return nil
        }
    }

    var filterValid: Bool {
        !triggerLabels.isEmpty && !triggerStates.isEmpty
            && !activeState.trimmingCharacters(in: .whitespaces).isEmpty
            && !doneState.trimmingCharacters(in: .whitespaces).isEmpty
            && !removalLabel.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var canSave: Bool {
        !submitting
            && !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !path.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && matchesValidName(name)
            && flags.allSatisfy { $0.keyValid && $0.valueValid }
            && Set(flags.map(\.key)).count == flags.count
            && nameServerError == nil
            && filterValid
    }

    private func matchesValidName(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9 ._\\-]+$", options: .regularExpression) != nil
    }

    // MARK: - Flag mutation

    func addFlag() { flags.append(FlagEntry()) }

    func removeFlag(_ id: UUID) { flags.removeAll { $0.id == id } }

    // MARK: - Body builders

    func toCreateBody() -> ProjectWrite {
        ProjectWrite(
            name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            path: path.trimmingCharacters(in: .whitespacesAndNewlines),
            linear_api_key: blankToNil(linearApiKey),
            conda_env: blankToNil(condaEnv),
            default_flags: flagsMap(),
            auto_scan_filter: currentFilterDto()
        )
    }

    func toUpdateBody() -> ProjectUpdate {
        guard let editing else { return ProjectUpdate() }
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedPath = path.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedConda = condaEnv.trimmingCharacters(in: .whitespacesAndNewlines)

        let nameOut = trimmedName != editing.name ? trimmedName : nil
        let pathOut = trimmedPath != editing.path ? trimmedPath : nil

        let keyOut: String?
        if clearLinearKey {
            keyOut = ""
        } else if !linearApiKey.isEmpty {
            keyOut = linearApiKey
        } else {
            keyOut = nil
        }

        let currentConda = editing.conda_env ?? ""
        let condaOut: String?
        if trimmedConda == currentConda {
            condaOut = nil
        } else if trimmedConda.isEmpty {
            condaOut = ""
        } else {
            condaOut = trimmedConda
        }

        return ProjectUpdate(
            name: nameOut,
            path: pathOut,
            linear_api_key: keyOut,
            conda_env: condaOut,
            default_flags: flagsDirty ? flagsMap() : nil,
            auto_scan_filter: filterDirty ? currentFilterDto() : nil
        )
    }

    private func flagsMap() -> [String: JSONValue] {
        var map: [String: JSONValue] = [:]
        for flag in flags { map[flag.key] = flag.toJSONValue() }
        return map
    }

    private func currentFilterDto() -> AutoScanFilterDto {
        AutoScanFilterDto(
            state_names: stateOptions.map(\.name).filter { triggerStates.contains($0) },
            label_names: labelOptions.filter { triggerLabels.contains($0) },
            active_state: activeState.trimmingCharacters(in: .whitespacesAndNewlines),
            done_state: doneState.trimmingCharacters(in: .whitespacesAndNewlines),
            removal_label: removalLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }

    private func blankToNil(_ value: String) -> String? {
        value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : value
    }

    // MARK: - Submit

    /// Returns true when the project was saved (the sheet should dismiss).
    func save() async -> Bool {
        submitAttempted = true
        nameServerError = nil
        submitError = nil
        guard canSave else { return false }
        submitting = true
        defer { submitting = false }
        do {
            if let editing {
                _ = try await repository.updateProject(currentName: editing.name, body: toUpdateBody())
            } else {
                createdProject = try await repository.createProject(toCreateBody())
            }
            return true
        } catch let error as ProjectConflictError {
            nameServerError = error.detail
            return false
        } catch {
            submitError = "Save failed: \(String(describing: error))"
            return false
        }
    }

    // MARK: - Linear metadata

    func loadMetadataIfNeeded() async {
        guard editing?.has_linear_key == true, discoveredLabels.isEmpty, discoveredStates.isEmpty else { return }
        await loadMetadata(forceRefresh: false)
    }

    func refreshMetadata() async {
        guard editing != nil else {
            message = "Save the project first to fetch labels/states from Linear."
            return
        }
        await loadMetadata(forceRefresh: true)
    }

    private func loadMetadata(forceRefresh: Bool) async {
        guard let projectName = editing?.name else { return }
        metadataLoading = true
        metadataError = nil
        defer { metadataLoading = false }
        async let labels = repository.linearLabels(projectName: projectName, forceRefresh: forceRefresh)
        async let states = repository.linearStates(projectName: projectName, forceRefresh: forceRefresh)
        do {
            discoveredLabels = try await labels
            discoveredStates = try await states
        } catch {
            metadataError = "Couldn’t load Linear labels/states."
        }
    }
}
