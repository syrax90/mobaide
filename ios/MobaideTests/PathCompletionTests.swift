import XCTest
@testable import Mobaide

/// The pure half of the folder suggestions: `PathCompletion` and the
/// `DirListingCache` reuse rule. Rule-for-rule mirror of web
/// `pathCompletion.test.ts` and Android `PathCompletionTest`; the cache runs on
/// a hand-driven clock so TTL cases never sleep.
final class PathCompletionTests: XCTestCase {
    private final class Clock: @unchecked Sendable {
        let start = ContinuousClock.now
        var offset: Duration = .zero
        var now: ContinuousClock.Instant { start + offset }
    }

    private let clock = Clock()
    private lazy var cache = DirListingCache(now: { [clock] in clock.now })

    private func listing(_ path: String, _ dirs: [String], truncated: Bool = false) -> FsDirsResponse {
        FsDirsResponse(path: path, dirs: dirs, truncated: truncated)
    }

    private func query(_ dir: String, _ fragment: String) -> PathCompletion.Query {
        PathCompletion.Query(dir: dir, fragment: fragment)
    }

    // MARK: - split

    func testSplitListsTheDirectoryUpToTheLastSeparator() {
        XCTAssertEqual(PathCompletion.split("/"), query("/", ""))
        XCTAssertEqual(PathCompletion.split("/Us"), query("/", "Us"))
        XCTAssertEqual(PathCompletion.split("/Users/pa"), query("/Users/", "pa"))
        XCTAssertEqual(PathCompletion.split("~/co"), query("~/", "co"))
        XCTAssertEqual(PathCompletion.split(#"C:\Users\Pa"#), query(#"C:\Users\"#, "Pa"))
        XCTAssertEqual(PathCompletion.split("c:/work/x"), query("c:/work/", "x"))
    }

    func testSplitTreatsABareTildeAsHome() {
        XCTAssertEqual(PathCompletion.split("~"), query("~/", ""))
    }

    // MARK: - rank

    func testRankPutsPrefixMatchesBeforeSubstringMatchesEachInTheAgentOrder() {
        XCTAssertEqual(
            PathCompletion.rank(["MyCode", "code-b", "barcode", "Code-a", "docs"], fragment: "code"),
            ["code-b", "Code-a", "MyCode", "barcode"]
        )
    }

    func testRankHidesDotDirectoriesUntilTheFragmentStartsWithADot() {
        let dirs = [".config", ".git", "config"]

        XCTAssertEqual(PathCompletion.rank(dirs, fragment: ""), ["config"])
        XCTAssertEqual(PathCompletion.rank(dirs, fragment: "."), [".config", ".git"])
        XCTAssertEqual(PathCompletion.rank(dirs, fragment: ".CON"), [".config"])
    }

    // MARK: - accept / trim

    func testSeparatorIsBackslashOnlyForAnAllBackslashPath() {
        XCTAssertEqual(PathCompletion.separator(listedPath: #"C:\Users\me\"#), "\\")
        XCTAssertEqual(PathCompletion.separator(listedPath: "C:/Users"), "/")
        XCTAssertEqual(PathCompletion.separator(listedPath: "/home/me"), "/")
        XCTAssertEqual(PathCompletion.separator(listedPath: ""), "/")
    }

    func testAcceptAppendsTheNameAndASeparatorInTheListedPathsOwnStyle() {
        XCTAssertEqual(PathCompletion.accept(listedPath: "/Users", name: "pavel"), "/Users/pavel/")
        XCTAssertEqual(PathCompletion.accept(listedPath: "/Users/", name: "pavel"), "/Users/pavel/")
        XCTAssertEqual(PathCompletion.accept(listedPath: "/", name: "Users"), "/Users/")
        XCTAssertEqual(PathCompletion.accept(listedPath: "/home/me", name: "code"), "/home/me/code/")
        XCTAssertEqual(PathCompletion.accept(listedPath: #"C:\Users"#, name: "me"), #"C:\Users\me\"#)
        XCTAssertEqual(PathCompletion.accept(listedPath: #"C:\"#, name: "Users"), #"C:\Users\"#)
        XCTAssertEqual(PathCompletion.accept(listedPath: "C:/Users", name: "me"), "C:/Users/me/")
    }

    func testTrimTrailingSeparatorDropsOneButKeepsRoots() {
        XCTAssertEqual(PathCompletion.trimTrailingSeparator("/a/b/"), "/a/b")
        XCTAssertEqual(PathCompletion.trimTrailingSeparator(#"C:\work\"#), #"C:\work"#)
        XCTAssertEqual(PathCompletion.trimTrailingSeparator("/a//"), "/a/")
        XCTAssertEqual(PathCompletion.trimTrailingSeparator("/a/b"), "/a/b")
        XCTAssertEqual(PathCompletion.trimTrailingSeparator("/"), "/")
        XCTAssertEqual(PathCompletion.trimTrailingSeparator(#"C:\"#), #"C:\"#)
        XCTAssertEqual(PathCompletion.trimTrailingSeparator("C:/"), "C:/")
    }

    // MARK: - DirListingCache

    func testCacheAnswersEveryFragmentFromOneCompleteUnfilteredListing() {
        cache.store(ListingRequest(dir: "/srv/", q: nil), listing: listing("/srv", ["a", "b"]))

        XCTAssertEqual(
            cache.lookup(dir: "/srv/", fragment: ""),
            ListingHit(listing: listing("/srv", ["a", "b"]), complete: true)
        )
        XCTAssertEqual(cache.lookup(dir: "/srv/", fragment: "zz")?.complete, true)
    }

    func testCacheMarksATruncatedListingAsShownButNotComplete() {
        cache.store(ListingRequest(dir: "/big/", q: nil), listing: listing("/big", ["a"], truncated: true))

        XCTAssertEqual(
            cache.lookup(dir: "/big/", fragment: "a"),
            ListingHit(listing: listing("/big", ["a"], truncated: true), complete: false)
        )
    }

    func testCacheLetsAFilteredListingAnswerOnlyFragmentsContainingItsQ() {
        cache.store(ListingRequest(dir: "/big/", q: "AB"), listing: listing("/big", ["abc", "xab"]))

        XCTAssertEqual(cache.lookup(dir: "/big/", fragment: "xaby")?.complete, true)
        XCTAssertEqual(cache.lookup(dir: "/big/", fragment: "ab")?.complete, true)
        XCTAssertNil(cache.lookup(dir: "/big/", fragment: "a"))
    }

    func testCacheNeverAnswersADirectoryFromItsChild() {
        cache.store(ListingRequest(dir: "/a/b/", q: nil), listing: listing("/a/b", ["c"]))

        XCTAssertNil(cache.lookup(dir: "/a/", fragment: ""))
        XCTAssertFalse(cache.hasUnfiltered(dir: "/a/"))
    }

    func testCacheKeepsAListingForTheTTLAndDropsItAfter() {
        cache.store(ListingRequest(dir: "/srv/", q: nil), listing: listing("/srv", ["a"]))

        clock.offset = PathCompletion.listingTTL
        XCTAssertEqual(cache.lookup(dir: "/srv/", fragment: "")?.complete, true)
        XCTAssertTrue(cache.hasUnfiltered(dir: "/srv/"))

        clock.offset = PathCompletion.listingTTL + .milliseconds(1)
        XCTAssertFalse(cache.hasUnfiltered(dir: "/srv/"))
        XCTAssertNil(cache.lookup(dir: "/srv/", fragment: ""))
    }

    func testCacheEvictsTheLeastRecentlyUsedPast32CountingALookupAsAUse() {
        cache.store(ListingRequest(dir: "/kept/", q: nil), listing: listing("/kept", ["k"]))
        for i in 0..<31 {
            cache.store(ListingRequest(dir: "/d\(i)/", q: nil), listing: listing("/d\(i)", []))
        }

        XCTAssertNotNil(cache.lookup(dir: "/kept/", fragment: ""))
        cache.store(ListingRequest(dir: "/new/", q: nil), listing: listing("/new", []))

        XCTAssertTrue(cache.hasUnfiltered(dir: "/kept/"))
        XCTAssertFalse(cache.hasUnfiltered(dir: "/d0/"))
        XCTAssertTrue(cache.hasUnfiltered(dir: "/d1/"))
    }

    // MARK: - nextRequest

    func testNextRequestListsANewDirectoryUnfiltered() {
        XCTAssertEqual(cache.nextRequest(query("/srv/", "pro")), ListingRequest(dir: "/srv/", q: nil))
    }

    func testNextRequestAsksNothingMoreWhileTypingInsideACompleteListing() {
        cache.store(ListingRequest(dir: "/srv/", q: nil), listing: listing("/srv", ["proj"]))

        XCTAssertNil(cache.nextRequest(query("/srv/", "")))
        XCTAssertNil(cache.nextRequest(query("/srv/", "pr")))
        XCTAssertNil(cache.nextRequest(query("/srv/", "proj")))
    }

    func testNextRequestReAsksATruncatedDirectoryWithTheFragmentAsQAndOnlyWithOne() {
        cache.store(ListingRequest(dir: "/big/", q: nil), listing: listing("/big", ["a"], truncated: true))

        XCTAssertNil(cache.nextRequest(query("/big/", "")))
        XCTAssertEqual(cache.nextRequest(query("/big/", "ab")), ListingRequest(dir: "/big/", q: "ab"))

        cache.store(ListingRequest(dir: "/big/", q: "ab"), listing: listing("/big", ["abc"]))
        XCTAssertNil(cache.nextRequest(query("/big/", "abc")))
    }
}
