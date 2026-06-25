"""Cover the launcher's on-boot garbage collection of old release dirs.

The launcher prunes superseded ``releases/<v>/`` dirs on every boot,
keeping the current version plus the rollback target (the highest-versioned
sibling with a usable venv). These tests poke ``_prune_old_releases`` and
``_resolve_keep_releases`` directly, building a fake ``<prefix>/releases``
layout the same way ``test_launcher_rollback.py`` does.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mobaide.launcher import __main__ as launcher
from tests.conftest import make_release, release_names


def test_prune_keeps_current_and_rollback_target(tmp_path):
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.2.0", keep=2)

    assert pruned == ["0.1.0"]
    assert set(kept) == {"0.2.0", "0.1.5"}
    assert release_names(tmp_path) == {"0.2.0", "0.1.5"}


def test_prune_ignores_incomplete_release(tmp_path):
    """A release swapped into place but not yet finalized (no ``.installed``
    sentinel, no console script) is invisible to prune: it is neither counted
    toward ``keep`` nor a deletion candidate — a concurrent updater may still be
    finalizing it, and reclaiming it is the updater's staleness-gated job."""
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")
    make_release(tmp_path, "0.3.0", complete=False)  # higher, but unfinalized

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.2.0", keep=2)

    # keep=2 retains current 0.2.0 + highest COMPLETE sibling 0.1.5; the oldest
    # complete release 0.1.0 is pruned. 0.3.0 is invisible to both lists and
    # survives untouched (its higher version does NOT consume a keep slot).
    assert pruned == ["0.1.0"]
    assert "0.3.0" not in kept
    assert (tmp_path / "releases" / "0.3.0").exists()
    assert release_names(tmp_path) == {"0.2.0", "0.1.5", "0.3.0"}


def test_prune_keeps_current_when_not_highest(tmp_path):
    """Post-rollback the current version is below a sibling on disk. The
    current version must survive, and the kept sibling is the rollback
    target (the highest other) — never the next-lower version."""
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.1.5", keep=2)

    assert pruned == ["0.1.0"]
    assert set(kept) == {"0.1.5", "0.2.0"}
    assert release_names(tmp_path) == {"0.1.5", "0.2.0"}


def test_prune_orders_versions_numerically_not_lexically(tmp_path):
    """Retention ranks releases by numeric version, not string order.

    Lexically ``"0.9.0" > "0.10.0" > "0.2.0"`` (because ``'9' > '1'``), which
    would prune the newest build ``0.10.0`` and keep an older one. The prune
    must instead keep the numerically-highest sibling (the real rollback
    target). With current ``0.10.0`` and keep=2 the kept set is
    ``{0.10.0, 0.9.0}`` and ``0.2.0`` is pruned."""
    make_release(tmp_path, "0.2.0")
    make_release(tmp_path, "0.9.0")
    make_release(tmp_path, "0.10.0")

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.10.0", keep=2)

    assert pruned == ["0.2.0"]
    assert set(kept) == {"0.10.0", "0.9.0"}
    assert release_names(tmp_path) == {"0.10.0", "0.9.0"}


def test_prune_keeps_numeric_rollback_target_when_current_not_highest(tmp_path):
    """Numeric ordering also drives the rollback-target pick after a rollback.

    Current is ``0.9.0`` while ``0.10.0`` (numerically newer, lexically older)
    is on disk. The kept set must be current ``0.9.0`` plus the numerically
    highest sibling ``0.10.0`` — never the lexical winner — pruning ``0.2.0``."""
    make_release(tmp_path, "0.2.0")
    make_release(tmp_path, "0.9.0")
    make_release(tmp_path, "0.10.0")

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.9.0", keep=2)

    assert pruned == ["0.2.0"]
    assert set(kept) == {"0.9.0", "0.10.0"}
    assert release_names(tmp_path) == {"0.9.0", "0.10.0"}


def test_prune_is_noop_with_one_release(tmp_path):
    make_release(tmp_path, "0.2.0")

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.2.0", keep=2)

    assert pruned == []
    assert set(kept) == {"0.2.0"}
    assert release_names(tmp_path) == {"0.2.0"}


def test_prune_is_noop_at_keep_count(tmp_path):
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.2.0")

    _, pruned = launcher._prune_old_releases(tmp_path, "0.2.0", keep=2)

    assert pruned == []
    assert release_names(tmp_path) == {"0.1.0", "0.2.0"}


def test_prune_leaves_foreign_and_incomplete_dirs(tmp_path):
    """Non-version dirs and a parseable-but-venvless dir (a foreign dir or
    a half-staged release) are never deletion candidates — only surplus
    valid releases beyond ``keep`` are pruned, and the skipped dirs survive
    untouched alongside them."""
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")
    (tmp_path / "releases" / "tmp").mkdir(parents=True)
    (tmp_path / "releases" / "bin").mkdir(parents=True)
    (tmp_path / "releases" / "0.0.9").mkdir(parents=True)  # no venv

    _, pruned = launcher._prune_old_releases(tmp_path, "0.2.0", keep=2)

    assert pruned == ["0.1.0"]
    assert release_names(tmp_path) == {"0.2.0", "0.1.5", "tmp", "bin", "0.0.9"}


def test_prune_skips_version_dir_without_venv(tmp_path):
    """A version dir lacking a venv interpreter is neither pruned nor counted
    as a keeper — only real releases participate. With three real releases and
    keep=2, the venvless ``0.2.0`` is ignored so the kept set is the current
    ``0.3.0`` plus the next real release ``0.1.5``, and ``0.1.0`` is pruned."""
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.3.0")
    (tmp_path / "releases" / "0.2.0").mkdir(parents=True)  # parseable, no venv

    _, pruned = launcher._prune_old_releases(tmp_path, "0.3.0", keep=2)

    assert pruned == ["0.1.0"]
    assert "0.2.0" in release_names(tmp_path)  # venvless dir untouched
    assert "0.3.0" in release_names(tmp_path)
    assert "0.1.5" in release_names(tmp_path)
    assert "0.1.0" not in release_names(tmp_path)


def test_prune_when_current_version_dir_absent(tmp_path):
    """The keep-set is seeded with ``current_version`` by name, so it consumes
    a keep slot even when no matching dir is on disk (a fresh-install pointer,
    or a current release whose venv is gone). With keep=2 that leaves room for
    exactly one real sibling (the newest, as the rollback target); the rest are
    pruned. The absent current is never itself a deletion candidate."""
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.3.0", keep=2)

    assert set(pruned) == {"0.1.5", "0.1.0"}
    assert kept == ["0.2.0"]
    assert release_names(tmp_path) == {"0.2.0"}


def test_prune_missing_releases_dir_is_noop(tmp_path):
    kept, pruned = launcher._prune_old_releases(tmp_path, "0.2.0", keep=2)
    assert (kept, pruned) == ([], [])


def test_prune_rmtree_failure_is_best_effort(tmp_path, monkeypatch, capsys):
    """A failed deletion (e.g. a Windows file lock) is logged and the dir
    retained; the next eligible release is still pruned and no error
    escapes."""
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")
    make_release(tmp_path, "0.3.0")

    def flaky_rmtree(path):
        if Path(path).name == "0.1.5":
            raise OSError("locked")

    monkeypatch.setattr(launcher.shutil, "rmtree", flaky_rmtree)

    kept, pruned = launcher._prune_old_releases(tmp_path, "0.3.0", keep=2)

    assert pruned == ["0.1.0"]
    assert "0.1.5" in kept
    assert "0.1.5" in capsys.readouterr().err


def test_prune_respects_keep_override(tmp_path):
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")
    make_release(tmp_path, "0.3.0")

    _, pruned = launcher._prune_old_releases(tmp_path, "0.3.0", keep=3)

    assert pruned == ["0.1.0"]
    assert release_names(tmp_path) == {"0.3.0", "0.2.0", "0.1.5"}


def test_prune_reads_keep_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv(launcher.KEEP_RELEASES_ENV, "3")
    make_release(tmp_path, "0.1.0")
    make_release(tmp_path, "0.1.5")
    make_release(tmp_path, "0.2.0")
    make_release(tmp_path, "0.3.0")

    _, pruned = launcher._prune_old_releases(tmp_path, "0.3.0")

    assert pruned == ["0.1.0"]
    assert release_names(tmp_path) == {"0.3.0", "0.2.0", "0.1.5"}


@pytest.mark.parametrize(
    "value,expected",
    [
        ("3", 3),
        ("5", 5),
        ("1", 2),   # clamped to the floor
        ("0", 2),   # clamped to the floor
        ("-4", 2),  # clamped to the floor
        ("abc", 2),  # non-int -> default
        ("", 2),     # empty -> default
    ],
)
def test_resolve_keep_releases(monkeypatch, value, expected):
    monkeypatch.setenv(launcher.KEEP_RELEASES_ENV, value)
    assert launcher._resolve_keep_releases() == expected


def test_resolve_keep_releases_defaults_when_unset(monkeypatch):
    monkeypatch.delenv(launcher.KEEP_RELEASES_ENV, raising=False)
    assert launcher._resolve_keep_releases() == launcher.DEFAULT_KEEP_RELEASES
