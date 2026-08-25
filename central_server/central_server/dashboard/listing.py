"""Query state of the users listing on the admin dashboard.

``UsersListing`` is the one value the page, the htmx table fragment and
the status poller all parse from the URL and render back into it: every
link, hidden input and poller URL in ``_users_table.html`` is built from a
single clamped instance, so the state a control submits is exactly the
state the server just rendered.
"""
from __future__ import annotations

import dataclasses
import math
from urllib.parse import urlencode

SORT_COLUMNS = ("registered", "activity")
DIRECTIONS = ("asc", "desc")
VERIFIED_FILTERS = ("all", "verified", "unverified")
PAGE_SIZES = (25, 50, 100)

DEFAULT_SORT = "registered"
DEFAULT_DIRECTION = "desc"
DEFAULT_PER_PAGE = 25

PAGE_PATH = "/dashboard"
FRAGMENT_PATH = "/dashboard/users"
STATUS_PATH = "/dashboard/agents-status"

# Query-string defaults, keyed by wire name. A parameter at its default is
# left out of every generated URL so the canonical address of the default
# view is plain ``/dashboard``.
_QUERY_DEFAULTS = {
    "q": "",
    "verified": "all",
    "sort": DEFAULT_SORT,
    "dir": DEFAULT_DIRECTION,
    "page": 1,
    "per_page": DEFAULT_PER_PAGE,
}


def _to_int(raw: str, fallback: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


@dataclasses.dataclass(frozen=True)
class UsersListing:
    q: str = ""
    verified: str = "all"
    sort: str = DEFAULT_SORT
    direction: str = DEFAULT_DIRECTION
    page: int = 1
    per_page: int = DEFAULT_PER_PAGE

    @classmethod
    def from_params(
        cls,
        *,
        q: str = "",
        verified: str = "all",
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
        page: str = "1",
        per_page: str = str(DEFAULT_PER_PAGE),
    ) -> UsersListing:
        """Parse raw query-string values leniently.

        Anything unrecognised falls back to its default rather than
        raising: a tampered or stale URL degrades to the default view
        instead of a 422 swapped into the table. ``page`` is only floored
        here; it is clamped to the last page once the total is known.
        """
        size = _to_int(per_page, DEFAULT_PER_PAGE)
        return cls(
            q=q,
            verified=verified if verified in VERIFIED_FILTERS else "all",
            sort=sort if sort in SORT_COLUMNS else DEFAULT_SORT,
            direction=(
                direction if direction in DIRECTIONS else DEFAULT_DIRECTION
            ),
            page=max(_to_int(page, 1), 1),
            per_page=size if size in PAGE_SIZES else DEFAULT_PER_PAGE,
        )

    @property
    def page_sizes(self) -> tuple[int, ...]:
        return PAGE_SIZES

    @property
    def verified_filter(self) -> bool | None:
        if self.verified == "verified":
            return True
        if self.verified == "unverified":
            return False
        return None

    @property
    def descending(self) -> bool:
        return self.direction == "desc"

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    def replace(self, **changes) -> UsersListing:
        return dataclasses.replace(self, **changes)

    def page_count(self, total: int) -> int:
        return max(1, math.ceil(total / self.per_page))

    def clamp(self, total: int) -> UsersListing:
        """Pull ``page`` back onto the last page when the URL points past
        it — e.g. after the last row of the last page was deleted."""
        return self.replace(page=min(self.page, self.page_count(total)))

    def first_row(self, total: int) -> int:
        return 0 if total == 0 else self.offset + 1

    def last_row(self, total: int) -> int:
        return min(self.offset + self.per_page, total)

    def page_links(self, total: int) -> list[int | None]:
        """Page numbers for the pager: first, last and a window of two
        around the current page, with ``None`` marking each elided gap."""
        last = self.page_count(total)
        wanted = {1, last} | {
            p for p in range(self.page - 2, self.page + 3) if 1 <= p <= last
        }
        links: list[int | None] = []
        previous = 0
        for number in sorted(wanted):
            if number - previous > 1:
                links.append(None)
            links.append(number)
            previous = number
        return links

    def sort_target(self, column: str) -> UsersListing:
        """The listing a click on ``column``'s header navigates to: the
        active column flips direction, any other column starts newest /
        most-recent first. Either way the pager restarts at page 1."""
        if column == self.sort:
            new_direction = "asc" if self.descending else "desc"
        else:
            new_direction = DEFAULT_DIRECTION
        return self.replace(sort=column, direction=new_direction, page=1)

    def aria_sort(self, column: str) -> str:
        if column != self.sort:
            return "none"
        return "descending" if self.descending else "ascending"

    def _query(self, overrides: dict) -> str:
        values = {
            "q": self.q,
            "verified": self.verified,
            "sort": self.sort,
            "dir": self.direction,
            "page": self.page,
            "per_page": self.per_page,
        }
        unknown = set(overrides) - set(values)
        if unknown:
            raise KeyError(f"Unknown listing parameters: {sorted(unknown)}")
        values.update(overrides)
        pairs = [
            (name, value)
            for name, value in values.items()
            if value is not None and value != _QUERY_DEFAULTS[name]
        ]
        return urlencode(pairs)

    def _url(self, path: str, overrides: dict) -> str:
        query = self._query(overrides)
        return f"{path}?{query}" if query else path

    def page_url(self, **overrides) -> str:
        """Full-page address (``/dashboard?…``); deep-linkable. An override
        of ``None`` drops that parameter from the URL."""
        return self._url(PAGE_PATH, overrides)

    def fragment_url(self, **overrides) -> str:
        """Address of the htmx table fragment (``/dashboard/users?…``)."""
        return self._url(FRAGMENT_PATH, overrides)

    def status_url(self) -> str:
        """Address the live-status poller hits for this exact page."""
        return self._url(STATUS_PATH, {})
