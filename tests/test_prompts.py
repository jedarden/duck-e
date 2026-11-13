"""Tests for the speech-oriented base system prompt."""

from pathlib import Path

from app.prompts import build_base_system_message


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
