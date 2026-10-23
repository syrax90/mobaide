import Foundation
import Observation

/// Drives one host-path field's folder suggestions (Android
/// `PathSuggestionsController`, web `usePathSuggestions`).
///
/// The fetch keys on the *request* (`dir`, `q`), not on the text, so typing
/// inside a directory already being listed leaves the in-flight listing alone,
/// while the shown list re-filters the cache on every keystroke.
@MainActor
@Observable
final class PathSuggestionsModel {
    private(set) var names: [String] = []
    private(set) var overflow = 0
    private(set) var listedPath = ""
    /// The agent host's path flavour, known once probed even while nothing is listed.
    private(set) var windowsPaths = false

    /// The separator shown after each name and written by a pick.
    var separator: String { PathCompletion.separator(listedPath: listedPath) }

    private let repository: PathSuggestionRepository
    private let sleeper: any Sleeper
    private var text = ""
    private var focused = false
    private var supported = false
    private var probeTask: Task<Void, Never>?
    private var fetchTask: Task<Void, Never>?
    /// The last request started. A failed one is not retried until the request
    /// itself changes, so a timed-out scan is not asked again on every keystroke.
    private var lastRequest: ListingRequest?

    init(repository: PathSuggestionRepository, sleeper: any Sleeper = ContinuousSleeper()) {
        self.repository = repository
        self.sleeper = sleeper
    }

    func textChanged(_ text: String) {
        self.text = text
        refresh()
    }

    func focusChanged(_ focused: Bool) {
        self.focused = focused
        if focused { probe() }
        refresh()
    }

    /// Asks the agent what it supports, unless it already said yes. Called when
    /// the form appears too, so the empty field's hint fits the host before focus.
    func probe() {
        guard !supported, probeTask == nil else { return }
        probeTask = Task { [weak self] in
            guard let self else { return }
            let support = await repository.fsSupport()
            supported = support.dirs
            windowsPaths = support.windowsPaths
            probeTask = nil
            refresh()
        }
    }

    /// The field's new text for a picked suggestion.
    func accept(_ name: String) -> String {
        PathCompletion.accept(listedPath: listedPath, name: name)
    }

    private func refresh() {
        let query = supported && focused ? PathCompletion.split(text) : nil
        render(query)
        let request = query.flatMap { repository.nextRequest($0) }
        guard request != lastRequest else { return }
        fetchTask?.cancel()
        lastRequest = request
        guard let request else { return }
        fetchTask = Task { [weak self, sleeper] in
            do {
                try await sleeper.sleep(for: PathCompletion.debounce)
            } catch {
                return
            }
            guard let self, !Task.isCancelled else { return }
            let stored = await repository.fetch(request)
            guard !Task.isCancelled else { return }
            if stored { refresh() }
        }
    }

    private func render(_ query: PathCompletion.Query?) {
        guard let query, let hit = repository.lookup(query) else {
            names = []
            overflow = 0
            return
        }
        let ranked = PathCompletion.rank(hit.listing.dirs, fragment: query.fragment)
        names = Array(ranked.prefix(PathCompletion.maxVisibleSuggestions))
        overflow = max(0, ranked.count - PathCompletion.maxVisibleSuggestions)
        listedPath = hit.listing.path
    }
}
