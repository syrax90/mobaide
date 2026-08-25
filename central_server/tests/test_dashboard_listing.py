"""Tests for ``UsersListing`` — the parsed, clamped query state that the
users page, its htmx fragment and the status poller all render from."""
from __future__ import annotations

import pytest

from central_server.dashboard.listing import UsersListing


class TestFromParams:
    def test_valid_values_pass_through(self):
        listing = UsersListing.from_params(
            q="bob", verified="unverified", sort="activity",
            direction="asc", page="3", per_page="100",
        )
        assert listing == UsersListing(
            q="bob", verified="unverified", sort="activity",
            direction="asc", page=3, per_page=100,
        )

    @pytest.mark.parametrize(
        "field, raw",
        [
            ("verified", "bogus"),
            ("sort", "email"),
            ("direction", "sideways"),
            ("per_page", "7"),
            ("per_page", "lots"),
        ],
    )
    def test_unknown_choice_falls_back_to_default(self, field, raw):
        assert UsersListing.from_params(**{field: raw}) == UsersListing()

    @pytest.mark.parametrize("raw", ["0", "-3", "abc", ""])
    def test_page_is_floored_at_one(self, raw):
        assert UsersListing.from_params(page=raw).page == 1

    @pytest.mark.parametrize(
        "verified, expected",
        [("all", None), ("verified", True), ("unverified", False)],
    )
    def test_verified_filter_maps_to_sql_tristate(self, verified, expected):
        assert UsersListing(verified=verified).verified_filter is expected


class TestPaging:
    def test_offset_and_row_range_follow_page(self):
        listing = UsersListing(page=2, per_page=25)
        assert listing.offset == 25
        assert listing.first_row(30) == 26
        assert listing.last_row(30) == 30

    def test_empty_total_reports_zero_rows_and_one_page(self):
        listing = UsersListing()
        assert listing.page_count(0) == 1
        assert listing.first_row(0) == 0
        assert listing.last_row(0) == 0

    @pytest.mark.parametrize("total, expected", [(25, 1), (26, 2), (50, 2)])
    def test_page_count_rounds_up(self, total, expected):
        assert UsersListing(per_page=25).page_count(total) == expected

    def test_clamp_pulls_page_past_end_back_to_last(self):
        assert UsersListing(page=99).clamp(30).page == 2
        assert UsersListing(page=99).clamp(0).page == 1

    def test_clamp_keeps_page_in_range(self):
        assert UsersListing(page=2).clamp(30).page == 2

    @pytest.mark.parametrize(
        "page, total, expected",
        [
            (5, 300, [1, None, 3, 4, 5, 6, 7, None, 12]),
            (1, 300, [1, 2, 3, None, 12]),
            (2, 300, [1, 2, 3, 4, None, 12]),
            (12, 300, [1, None, 10, 11, 12]),
            (1, 75, [1, 2, 3]),
            (1, 0, [1]),
        ],
    )
    def test_page_links_window_with_gaps(self, page, total, expected):
        assert UsersListing(page=page).page_links(total) == expected


class TestSortTarget:
    def test_active_column_flips_direction_and_resets_page(self):
        listing = UsersListing(sort="registered", direction="desc", page=4)
        target = listing.sort_target("registered")
        assert (target.sort, target.direction, target.page) == (
            "registered", "asc", 1,
        )
        assert target.sort_target("registered").direction == "desc"

    def test_other_column_starts_descending(self):
        listing = UsersListing(sort="registered", direction="asc", page=4)
        target = listing.sort_target("activity")
        assert (target.sort, target.direction, target.page) == (
            "activity", "desc", 1,
        )

    def test_sort_target_keeps_filters_and_page_size(self):
        listing = UsersListing(q="bob", verified="verified", per_page=50)
        target = listing.sort_target("activity")
        assert (target.q, target.verified, target.per_page) == (
            "bob", "verified", 50,
        )

    def test_aria_sort_names_only_the_active_column(self):
        listing = UsersListing(sort="activity", direction="asc")
        assert listing.aria_sort("activity") == "ascending"
        assert listing.aria_sort("registered") == "none"
        assert listing.replace(direction="desc").aria_sort("activity") == (
            "descending"
        )


class TestUrls:
    def test_default_view_is_the_bare_path(self):
        listing = UsersListing()
        assert listing.page_url() == "/dashboard"
        assert listing.fragment_url() == "/dashboard/users"
        assert listing.status_url() == "/dashboard/agents-status"

    def test_only_non_default_params_are_emitted(self):
        listing = UsersListing(
            q="bob", verified="all", sort="activity", direction="desc",
            page=3, per_page=50,
        )
        assert listing.page_url() == (
            "/dashboard?q=bob&sort=activity&page=3&per_page=50"
        )

    def test_status_url_carries_the_full_page_state(self):
        listing = UsersListing(
            q="a", verified="unverified", sort="activity", direction="asc",
            page=2, per_page=100,
        )
        assert listing.status_url() == (
            "/dashboard/agents-status"
            "?q=a&verified=unverified&sort=activity&dir=asc&page=2&per_page=100"
        )

    def test_override_replaces_and_none_drops_a_param(self):
        listing = UsersListing(page=3, per_page=50)
        assert listing.fragment_url(page=1, per_page=None) == "/dashboard/users"
        assert listing.page_url(page=2) == "/dashboard?page=2&per_page=50"

    def test_override_back_to_default_is_omitted(self):
        assert UsersListing(page=3).page_url(page=1) == "/dashboard"

    def test_query_text_is_url_encoded(self):
        assert UsersListing(q="a b&c=d").page_url() == "/dashboard?q=a+b%26c%3Dd"

    def test_unknown_override_raises(self):
        with pytest.raises(KeyError):
            UsersListing().page_url(limit=10)
