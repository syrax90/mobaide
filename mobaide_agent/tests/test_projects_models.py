"""Unit tests for the Pydantic wire models defined in
``mobaide.agent.projects.models``.

The REST integration tests in ``test_rest_projects.py`` exercise these
models indirectly through HTTP. These tests pin the dataclass <-> wire
model translation directly so a refactor of either side surfaces here
before it cascades into a wire-format regression.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mobaide.agent.projects.models import AutoScanFilterModel
from mobaide.core.projects_config import AutoScanFilter


class TestAutoScanFilterModelDefaults:
    """The wire model must default to the same values as the dataclass so
    a client that omits the entire ``auto_scan_filter`` block lands on the
    historical hardcoded behavior identical to a legacy on-disk record."""

    def test_default_values_match_dataclass(self):
        model = AutoScanFilterModel()
        defaults = AutoScanFilter()
        assert model.state_names == defaults.state_names
        assert model.label_names == defaults.label_names
        assert model.active_state == defaults.active_state
        assert model.done_state == defaults.done_state
        assert model.removal_label == defaults.removal_label


class TestAutoScanFilterModelRoundTrip:
    """The wire model is the only translation layer between HTTP JSON and
    the immutable :class:`AutoScanFilter` dataclass that the scanner reads.
    Both directions must preserve every field byte-for-byte; otherwise an
    edit cycle (GET -> form -> PUT) would silently mutate fields."""

    def test_from_dataclass_preserves_all_fields(self):
        f = AutoScanFilter(
            state_names=["Proposals", "Backlog"],
            label_names=["AutoPipeline", "AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )

        m = AutoScanFilterModel.from_dataclass(f)

        assert m.state_names == ["Proposals", "Backlog"]
        assert m.label_names == ["AutoPipeline", "AI-ready"]
        assert m.active_state == "Doing"
        assert m.done_state == "Released"
        assert m.removal_label == "AI-ready"

    def test_to_dataclass_preserves_all_fields(self):
        m = AutoScanFilterModel(
            state_names=["Triage"],
            label_names=["AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )

        f = m.to_dataclass()

        assert isinstance(f, AutoScanFilter)
        assert f.state_names == ["Triage"]
        assert f.label_names == ["AI-ready"]
        assert f.active_state == "Doing"
        assert f.done_state == "Released"
        assert f.removal_label == "AI-ready"

    def test_round_trip_is_identity(self):
        # GET (dataclass -> wire) followed by PUT (wire -> dataclass)
        # must reproduce the original filter exactly.
        original = AutoScanFilter(
            state_names=["Proposals", "Backlog"],
            label_names=["AutoPipeline", "AI-ready"],
            active_state="Doing",
            done_state="Released",
            removal_label="AI-ready",
        )

        wire = AutoScanFilterModel.from_dataclass(original)
        restored = wire.to_dataclass()

        assert restored == original

    def test_to_dataclass_decouples_lists(self):
        # The dataclass is frozen, but its list fields are mutable
        # objects. ``to_dataclass`` must hand out fresh copies so a later
        # mutation of the wire model's list does not silently rewrite the
        # filter the scanner already snapshotted.
        m = AutoScanFilterModel(
            state_names=["Proposals"],
            label_names=["AutoPipeline"],
            active_state="In Progress",
            done_state="Done",
            removal_label="AutoPipeline",
        )

        f = m.to_dataclass()
        m.state_names.append("Backlog")

        assert f.state_names == ["Proposals"]


class TestAutoScanFilterModelValidation:
    """The wire model validates list/string min-length at the API boundary
    so a malformed body is rejected with 422 before any patch is applied
    to the store. These are pinned at unit level (no HTTP) so a future
    move of the validators stays detectable here."""

    def test_empty_state_names_rejected(self):
        with pytest.raises(ValidationError):
            AutoScanFilterModel(
                state_names=[],
                label_names=["AutoPipeline"],
                active_state="In Progress",
                done_state="Done",
                removal_label="AutoPipeline",
            )

    def test_empty_label_names_rejected(self):
        with pytest.raises(ValidationError):
            AutoScanFilterModel(
                state_names=["Proposals"],
                label_names=[],
                active_state="In Progress",
                done_state="Done",
                removal_label="AutoPipeline",
            )

    @pytest.mark.parametrize(
        "field",
        ["active_state", "done_state", "removal_label"],
    )
    def test_empty_string_singleton_rejected(self, field):
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = ""
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)


class TestAutoScanFilterModelElementValidators:
    """``min_length=1`` only validates list LENGTH, not element content. The
    explicit ``_reject_empty_elements`` validator on ``state_names`` and
    ``label_names`` is what guards against ``[""]`` flowing onto disk and
    producing a permanently-no-match scanner state once the empty string
    reaches Linear's filter dict."""

    @pytest.mark.parametrize(
        "field", ["state_names", "label_names"],
    )
    def test_empty_string_element_in_list_rejected(self, field):
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = [""]
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)

    @pytest.mark.parametrize(
        "field", ["state_names", "label_names"],
    )
    def test_whitespace_only_element_in_list_rejected(self, field):
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = ["   "]
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)

    @pytest.mark.parametrize(
        "field", ["state_names", "label_names"],
    )
    def test_mixed_valid_and_empty_element_rejected(self, field):
        # The validator must reject the WHOLE list if any element is empty —
        # silently dropping the bad element would be worse (the user thinks
        # they configured a 2-state filter but only one is active).
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = ["Proposals", ""]
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)

    @pytest.mark.parametrize(
        "field", ["active_state", "done_state", "removal_label"],
    )
    def test_whitespace_only_singleton_rejected(self, field):
        kwargs = {
            "state_names": ["Proposals"],
            "label_names": ["AutoPipeline"],
            "active_state": "In Progress",
            "done_state": "Done",
            "removal_label": "AutoPipeline",
        }
        kwargs[field] = "   "
        with pytest.raises(ValidationError):
            AutoScanFilterModel(**kwargs)
