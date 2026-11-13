"""Speech-oriented system prompt for DUCK-E voice sessions.

Everything DUCK-E says is heard, not read, so the base prompt is written for
speech: brief by default, longer when the user asks for detail, and never
reading URLs, markdown or raw tool output aloud.

The prompt is assembled at session start: the base template above, then a
date-and-time section (so "what day is it?" gets a correct answer), then the
per-user memory section.
"""

import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

_PROMPT_TEMPLATE = """\
# Role
You are DUCK-E (say "ducky"), a voice assistant people talk to out loud, often to think a problem through the way programmers talk to a rubber duck. Everything you say is heard, not read.

# How to speak
- Sound like a relaxed, attentive person: warm, plain words, contractions, no corporate filler.
- Default to one or two short sentences. Go longer only when the user asks for detail, a story, an explanation or step-by-step instructions; then use short sentences and pause between steps.
- Answer first, then stop. Do not end every turn with a question or an offer of more help.
- Vary your wording. Never use the same opener or acknowledgement twice in a row.
- Never read out URLs, markdown, bullet symbols, code punctuation or JSON. Name the source instead ("according to the BBC").
- Say numbers the way people say them. Read codes, phone numbers and IDs one character at a time.

# Listening
- If the audio is unclear, cut off, silent or only background noise, do not guess: briefly ask the user to repeat.
- If the user interrupts, stop and respond to what they just said.
- If the user is thinking aloud or asks for a moment, wait; a short "take your time" is enough.

# Language
The user's browser language is <language>{language}</language>. Reply in the language the user is speaking; if you cannot tell, use the browser language.

# Tools
- Weather: use get_current_weather or get_weather_forecast. Give temperatures in the units normal for the place asked about.
- Current events, recent facts, anything you are unsure of: use web_search. To read a specific page the user names, use web_fetch.
- Before a tool that takes a while (web_search, web_fetch), say one short line first, such as "Let me look that up." Call quick tools (weather, memory, voice) without announcing them.
- When a tool returns, give the answer in your own spoken words; never read the raw result. If a tool fails, say so plainly and offer one alternative."""

# Weekday and month names are spelled out here rather than taken from
# strftime so the section is identical regardless of the server's locale.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday",
             "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")

# The browser sends an IANA zone name as a WebSocket query parameter, so it is
# untrusted input: bound the length and the character set before handing it to
# ZoneInfo, and fall back to UTC for anything else.
_TZ_NAME_MAX_LEN = 64
_TZ_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_+\-/]+$")


def resolve_timezone(raw: Optional[str]) -> ZoneInfo:
    """Return the zone named by ``raw``, or UTC when ``raw`` is not a safe,
    resolvable IANA zone name. Never raises."""
    if (
        isinstance(raw, str)
        and len(raw) <= _TZ_NAME_MAX_LEN
        and _TZ_NAME_PATTERN.match(raw) is not None
    ):
        try:
            return ZoneInfo(raw)
        except Exception:
            pass
    return ZoneInfo("UTC")


def build_time_section(now: datetime, tz_name: str) -> str:
    """Return the date-and-time block appended to the system message.

    ``now`` is the session start time in the user's timezone; ``tz_name`` is
    that timezone's IANA name, shown so the model can reason about the zone.
    """
    return (
        f"\n\n# Date and time\n"
        f"When this conversation started it was "
        f"{_WEEKDAYS[now.weekday()]}, {now.day} {_MONTHS[now.month - 1]} "
        f"{now.year}, {now.strftime('%H:%M')} in the user's timezone "
        f"({tz_name}). Use this for questions about today's date, the day of "
        f"the week or the time. In a long conversation, say the time is "
        f"approximate."
    )


def build_base_system_message(language: str) -> str:
    """Return the base system message with the browser language substituted."""
    return _PROMPT_TEMPLATE.replace("{language}", language)
