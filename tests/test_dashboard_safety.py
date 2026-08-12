"""Static guards on the dashboard.

The board is projected in a room full of people who control one of its inputs
(their team name), so the escaping rules matter more than usual. These are
source-level assertions — cheap, and they fail loudly if anyone reintroduces
the old rendering approach.
"""

import re
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard.html"


@pytest.fixture(scope="module")
def source():
    return DASHBOARD.read_text()


def test_team_names_are_written_with_textcontent(source):
    assert "refs.label.textContent" in source


def test_no_interpolation_of_team_data_into_innerhtml(source):
    """`innerHTML` is allowed only for the fixed card skeleton, never with data."""
    for match in re.finditer(r"innerHTML\s*=\s*([^;]+);", source, re.S):
        assigned = match.group(1)
        assert "${" not in assigned, f"template interpolation into innerHTML: {assigned[:80]}"
        assert "t." not in assigned, f"team data flowed into innerHTML: {assigned[:80]}"


def test_no_template_literals_carry_markup(source):
    """The previous build assembled whole cards from backtick templates."""
    assert not re.search(r"`[^`]*<(div|span|h2|article)[^`]*\$\{", source, re.S)


def test_cards_are_built_as_nodes(source):
    assert "createElement" in source


def test_no_third_party_scripts(source):
    """A CDN that fails at event time should not be able to break the board."""
    scripts = re.findall(r"<script[^>]*\ssrc=", source)
    assert scripts == [], f"external script tags present: {scripts}"


def test_no_remote_code_execution_helpers(source):
    for banned in ("eval(", "new Function(", "document.write"):
        assert banned not in source, banned


def test_dashboard_does_not_contain_the_narrative(source, narrative):
    assert narrative.lower() not in source.lower()


def test_polls_status_and_nothing_else(source):
    fetches = re.findall(r"fetch\(\s*[\"']([^\"']+)", source)
    assert fetches == ["/status"]


def test_uses_the_unique_word_metric(source):
    """Progress must track distinct words, not raw request count."""
    assert "unique_seen" in source
    assert "seen_count" not in source


def test_handles_a_failed_poll(source):
    assert "catch" in source
    assert "Connection lost" in source


def test_animation_is_deliberately_unconditional(source):
    """The board is a projected display, where the motion is part of the point,
    so it is not gated on prefers-reduced-motion. This is a product decision
    rather than an oversight — worth revisiting if this ever becomes a page
    people sit and browse rather than glance at across a room."""
    assert "prefers-reduced-motion" not in source


def test_all_three_team_states_are_represented(source):
    """Completed, decoding and idle each need their own icon."""
    for cls in ("i-done", "i-decoding", "i-idle"):
        assert cls in source, cls
    assert "idle_seconds" in source
    assert "IDLE_AFTER" in source


def test_state_labels_are_exposed_to_screen_readers(source):
    assert "sr-only" in source
    assert "stateText.textContent" in source


def test_cards_are_reused_between_polls(source):
    """Wiping the grid each poll would restart every bar transition."""
    assert 'innerHTML=""' not in source.replace(" ", "")
    assert "cards.get(" in source
