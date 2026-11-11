import XCTest
@testable import Mobaide

/// The picker's grouping is a cross-client contract, so these are the
/// expectations `web/src/api/templateRepository.groupTemplates` and Android's
/// `TemplatesPickerSheet.groupTemplates` are already held to.
final class TemplatesPickerGroupingTests: XCTestCase {

    private func row(
        _ name: String,
        body: String = "echo hi",
        favorite: Bool = false,
        lastUsed: String? = nil
    ) -> TemplateDto {
        TemplateDto(
            template_id: "id-\(name)",
            name: name,
            body: body,
            is_favorite: favorite,
            last_used_at: lastUsed,
            created_at: "t",
            updated_at: "t",
            version: 1
        )
    }

    // MARK: - Recents

    func testRecentsKeepTheFiveNewestAndDropTheRestIntoAll() {
        let all = (1...7).map { row("t\($0)", lastUsed: "2026-08-0\($0)T00:00:00Z") }

        let grouped = TemplateGrouping.group(all, query: "")

        XCTAssertEqual(grouped.recents.map(\.name), ["t7", "t6", "t5", "t4", "t3"])
        // Past the cap a row is demoted, never dropped: the picker is the only
        // place these templates can be reached.
        XCTAssertEqual(grouped.others.map(\.name), ["t1", "t2"])
    }

    func testNeverUsedTemplatesStayOutOfRecents() {
        let all = [row("alpha"), row("beta", lastUsed: "2026-08-01T00:00:00Z")]

        let grouped = TemplateGrouping.group(all, query: "")

        XCTAssertEqual(grouped.recents.map(\.name), ["beta"])
        XCTAssertEqual(grouped.others.map(\.name), ["alpha"])
    }

    // MARK: - Section exclusivity

    func testRecentlyUsedFavoriteRendersOnlyInRecents() {
        // The exclusions are the whole point: without them this row appeared in
        // all three sections at once.
        let all = [
            row("alpha", favorite: true, lastUsed: "2026-08-01T00:00:00Z"),
            row("beta", favorite: true),
            row("gamma"),
        ]

        let grouped = TemplateGrouping.group(all, query: "")

        XCTAssertEqual(grouped.recents.map(\.name), ["alpha"])
        XCTAssertEqual(grouped.favorites.map(\.name), ["beta"])
        XCTAssertEqual(grouped.others.map(\.name), ["gamma"])
    }

    // MARK: - Query

    func testQueryMatchesNameOrBodyCaseInsensitivelyAfterTrimming() {
        let all = [
            row("Standup", body: "git status"),
            row("Deploy", body: "make RELEASE"),
        ]

        XCTAssertEqual(TemplateGrouping.group(all, query: "  stand  ").others.map(\.name), ["Standup"])
        XCTAssertEqual(TemplateGrouping.group(all, query: "release").others.map(\.name), ["Deploy"])
    }
}
