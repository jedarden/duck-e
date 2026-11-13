"""Tests for the speech-oriented base system prompt."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.prompts import build_base_system_message, build_time_section, resolve_timezone


def test_prompt_localizes_browser_language():
    message = build_base_system_message("fr-FR")

    assert "<language>fr-FR</language>" in message


def test_prompt_names_all_registered_tools():
    message = build_base_system_message("en-US")

    for tool in ("get_current_weather", "get_weather_forecast", "web_search", "web_fetch"):
        assert tool in message


def test_prompt_asks_for_repeat_on_unclear_audio():
    message = build_base_system_message("en-US")

    assert "ask the user to repeat" in message


def test_prompt_drops_old_brevity_cap():
    message = build_base_system_message("en-US")

    assert "two short sentences maximum" not in message


def test_prompt_has_no_unreplaced_language_placeholder():
    message = build_base_system_message("fr-FR")

    assert "{language}" not in message


def test_main_no_longer_carries_the_old_inline_prompt():
    assert "MUST first speak" not in Path("app/main.py").read_text()


class TestResolveTimezone:
    """resolve_timezone turns the untrusted tz query parameter into a zone."""

    def test_returns_named_zone_for_valid_names(self):
        assert resolve_timezone("America/New_York") == ZoneInfo("America/New_York")
        assert resolve_timezone("Asia/Kolkata") == ZoneInfo("Asia/Kolkata")

    def test_returns_utc_for_missing_or_empty(self):
        assert resolve_timezone(None) == ZoneInfo("UTC")
        assert resolve_timezone("") == ZoneInfo("UTC")

    def test_returns_utc_for_unresolvable_name(self):
        assert resolve_timezone("Not/AZone") == ZoneInfo("UTC")

    def test_returns_utc_for_path_traversal(self):
        assert resolve_timezone("../../etc/passwd") == ZoneInfo("UTC")

    def test_returns_utc_for_overlong_name(self):
        assert resolve_timezone("A" * 100) == ZoneInfo("UTC")

    def test_returns_utc_for_non_string(self):
        for raw in (123, b"America/New_York", ["America/New_York"], {"tz": 1}):
            assert resolve_timezone(raw) == ZoneInfo("UTC")


class TestBuildTimeSection:
    """build_time_section renders the session-start time for the system prompt."""

    def test_contains_weekday_date_time_and_zone(self):
        section = build_time_section(
            datetime(2026, 11, 12, 14, 5, tzinfo=ZoneInfo("America/New_York")),
            "America/New_York",
        )

        assert "Thursday" in section
        assert "12 November 2026" in section
        assert "14:05" in section
        assert "America/New_York" in section

    def test_starts_as_a_new_prompt_section(self):
        section = build_time_section(
            datetime(2026, 1, 1, 0, 0, tzinfo=ZoneInfo("UTC")), "UTC"
        )

        assert section.startswith("\n\n# Date and time\n")

    def test_no_leading_zero_on_the_day(self):
        section = build_time_section(
            datetime(2026, 3, 7, 9, 1, tzinfo=ZoneInfo("UTC")), "UTC"
        )

        assert "7 March 2026" in section
        assert "09:01" in section
