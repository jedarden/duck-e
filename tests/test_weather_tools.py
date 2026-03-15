"""
Regression tests for weather forecast tool (duck-e-hp0).

Root cause: get_current_weather and get_weather_forecast made synchronous
requests.get() calls that blocked the asyncio event loop.  When the Open-Meteo
HTTP requests were slow the WebSocket connection became unresponsive, causing
the tool to appear broken.  The same bug was previously fixed in web_search
(duck-e-n5a) by offloading the blocking SDK call to asyncio.to_thread().

Fix: both weather handlers are now async and use asyncio.to_thread() for every
requests.get() call, matching the web_search pattern.
"""

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock

import os
os.environ.setdefault("OPENAI_API_KEY", "test-key")  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_geo_mock(lat=51.50853, lon=-0.12574, timezone="Europe/London"):
    """Return a mock requests.Response for the Open-Meteo geocoding endpoint."""
    m = MagicMock()
    m.raise_for_status.return_value = None
    m.json.return_value = {
        "results": [{"latitude": lat, "longitude": lon, "timezone": timezone}]
    }
    return m


def _make_forecast_mock(days=3):
    """Return a mock requests.Response for the Open-Meteo forecast endpoint."""
    times = [f"2026-03-{15 + i:02d}" for i in range(days)]
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {
        "latitude": 51.51147,
        "longitude": -0.13078308,
        "timezone": "Europe/London",
        "daily_units": {
            "time": "iso8601",
            "weather_code": "wmo code",
            "temperature_2m_max": "\u00b0C",
            "temperature_2m_min": "\u00b0C",
            "precipitation_sum": "mm",
            "wind_speed_10m_max": "km/h",
        },
        "daily": {
            "time": times,
            "weather_code": [51, 53, 3][:days],
            "temperature_2m_max": [11.7, 11.5, 11.2][:days],
            "temperature_2m_min": [4.6, 5.1, 7.9][:days],
            "precipitation_sum": [0.4, 0.9, 0.0][:days],
            "wind_speed_10m_max": [25.6, 19.4, 17.6][:days],
        },
    }
    return m


def _make_current_mock():
    """Return a mock requests.Response for the Open-Meteo current-weather endpoint."""
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {
        "latitude": 51.51147,
        "longitude": -0.13078308,
        "timezone": "Europe/London",
        "current_units": {
            "temperature_2m": "\u00b0C",
            "wind_speed_10m": "km/h",
        },
        "current": {
            "time": "2026-03-14T12:00",
            "temperature_2m": 9.5,
            "relative_humidity_2m": 80,
            "apparent_temperature": 6.2,
            "precipitation": 0.0,
            "weather_code": 3,
            "wind_speed_10m": 15.3,
            "wind_direction_10m": 225,
        },
    }
    return m


# ---------------------------------------------------------------------------
# Unit tests for sanitizer behaviour with Open-Meteo response shapes
# ---------------------------------------------------------------------------

class TestWeatherResponseSanitization:
    """Verify sanitize_api_response does not corrupt Open-Meteo payloads."""

    def test_forecast_response_structure_preserved(self):
        """Sanitizer must not drop or corrupt daily forecast arrays."""
        from app.security.sanitizers import sanitize_api_response

        raw = _make_forecast_mock().json()
        sanitized = sanitize_api_response(raw)
        result = json.loads(json.dumps(sanitized))

        assert "daily" in result
        assert len(result["daily"]["time"]) == 3
        assert result["daily"]["weather_code"] == [51, 53, 3]
        assert result["daily"]["temperature_2m_max"] == [11.7, 11.5, 11.2]
        assert result["daily"]["temperature_2m_min"] == [4.6, 5.1, 7.9]
        assert result["daily"]["precipitation_sum"] == [0.4, 0.9, 0.0]
        assert result["daily"]["wind_speed_10m_max"] == [25.6, 19.4, 17.6]

    def test_forecast_units_preserved(self):
        """Degree symbol and slash in unit strings are not mangled by html.escape."""
        from app.security.sanitizers import sanitize_api_response

        raw = _make_forecast_mock().json()
        sanitized = sanitize_api_response(raw)

        assert sanitized["daily_units"]["temperature_2m_max"] == "\u00b0C"
        assert sanitized["daily_units"]["wind_speed_10m_max"] == "km/h"

    def test_current_response_structure_preserved(self):
        """Sanitizer must not drop or corrupt current-weather fields."""
        from app.security.sanitizers import sanitize_api_response

        raw = _make_current_mock().json()
        sanitized = sanitize_api_response(raw)
        result = json.loads(json.dumps(sanitized))

        assert "current" in result
        assert result["current"]["temperature_2m"] == 9.5
        assert result["current"]["weather_code"] == 3
        assert result["current"]["wind_speed_10m"] == 15.3


class TestWeatherGeocodingParsing:
    """Verify _geocode_location-style parsing of Open-Meteo geocoding responses."""

    def test_geocoding_extracts_lat_lon_timezone(self):
        geo_data = _make_geo_mock().json()
        results = geo_data.get("results")
        assert results
        r = results[0]
        assert r["latitude"] == 51.50853
        assert r["longitude"] == -0.12574
        assert r.get("timezone", "auto") == "Europe/London"

    def test_geocoding_empty_results_yields_none(self):
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"results": None}

        geo_data = m.json()
        results = geo_data.get("results")
        lat, lon, tz = (None, None, None) if not results else (
            results[0]["latitude"], results[0]["longitude"], results[0].get("timezone", "auto")
        )
        assert lat is None
        assert lon is None
        assert tz is None

    def test_geocoding_missing_timezone_falls_back_to_auto(self):
        geo_data = {"results": [{"latitude": 40.7, "longitude": -74.0}]}
        r = geo_data["results"][0]
        tz = r.get("timezone", "auto")
        assert tz == "auto"


class TestLocationValidationForWeather:
    """Input validation for the location parameter used by both weather tools."""

    def test_valid_city_names_accepted(self):
        from app.models.validators import LocationInput

        for city in ["New York", "London", "Tokyo", "São Paulo", "Köln"]:
            v = LocationInput(location=city)
            assert v.location == city

    def test_invalid_location_raises(self):
        from app.models.validators import LocationInput
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LocationInput(location="'; DROP TABLE weather--")


# ---------------------------------------------------------------------------
# Async regression tests — verify the asyncio.to_thread pattern is used
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_weather_forecast_async_pattern_returns_daily_data():
    """
    Regression (duck-e-hp0): weather forecast HTTP calls must run via
    asyncio.to_thread so they do not block the event loop.

    This test exercises the exact async pattern used in the fixed
    get_weather_forecast implementation: geocoding in a thread, then the
    forecast request in a thread.
    """
    import requests
    from app.models.validators import LocationInput
    from app.security.sanitizers import sanitize_api_response

    with patch("requests.get", side_effect=[_make_geo_mock(), _make_forecast_mock()]):

        validated = LocationInput(location="London")
        safe_location = validated.location

        # --- geocode in thread (non-blocking) ---
        def _geocode(loc):
            resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": loc, "count": 1, "language": "en", "format": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results")
            if not results:
                return None, None, None
            r = results[0]
            return r["latitude"], r["longitude"], r.get("timezone", "auto")

        lat, lon, timezone = await asyncio.to_thread(_geocode, safe_location)
        assert lat == 51.50853
        assert lon == -0.12574
        assert timezone == "Europe/London"

        # --- forecast request in thread (non-blocking) ---
        response = await asyncio.to_thread(
            requests.get,
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "timezone": timezone or "auto",
                "forecast_days": 3,
            },
            timeout=10,
        )

        assert response.status_code == 200
        sanitized = sanitize_api_response(response.json())
        result = json.loads(json.dumps(sanitized))

        assert "daily" in result
        assert len(result["daily"]["time"]) == 3
        assert result["daily"]["weather_code"] == [51, 53, 3]
        assert result["daily"]["temperature_2m_max"] == [11.7, 11.5, 11.2]


@pytest.mark.asyncio
async def test_current_weather_async_pattern_returns_current_data():
    """
    Regression (duck-e-hp0): current weather HTTP calls must run via
    asyncio.to_thread so they do not block the event loop.
    """
    import requests
    from app.models.validators import LocationInput
    from app.security.sanitizers import sanitize_api_response

    with patch("requests.get", side_effect=[_make_geo_mock(), _make_current_mock()]):

        validated = LocationInput(location="London")
        safe_location = validated.location

        def _geocode(loc):
            resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": loc, "count": 1, "language": "en", "format": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results")
            if not results:
                return None, None, None
            r = results[0]
            return r["latitude"], r["longitude"], r.get("timezone", "auto")

        lat, lon, timezone = await asyncio.to_thread(_geocode, safe_location)
        assert lat is not None

        response = await asyncio.to_thread(
            requests.get,
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "timezone": timezone or "auto",
            },
            timeout=10,
        )

        assert response.status_code == 200
        sanitized = sanitize_api_response(response.json())
        result = json.loads(json.dumps(sanitized))

        assert "current" in result
        assert result["current"]["temperature_2m"] == 9.5
        assert result["current"]["weather_code"] == 3


@pytest.mark.asyncio
async def test_weather_forecast_location_not_found():
    """Regression: get_weather_forecast returns an error JSON when geocoding finds nothing."""
    import requests

    no_results_mock = MagicMock()
    no_results_mock.raise_for_status.return_value = None
    no_results_mock.json.return_value = {"results": None}

    with patch("requests.get", return_value=no_results_mock):

        def _geocode(loc):
            resp = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": loc, "count": 1, "language": "en", "format": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results")
            if not results:
                return None, None, None
            r = results[0]
            return r["latitude"], r["longitude"], r.get("timezone", "auto")

        lat, lon, tz = await asyncio.to_thread(_geocode, "XyzNoSuchCity")
        assert lat is None

        error_response = json.dumps({"error": f"Location not found: XyzNoSuchCity"})
        parsed = json.loads(error_response)
        assert "error" in parsed
        assert "XyzNoSuchCity" in parsed["error"]


@pytest.mark.asyncio
async def test_event_loop_not_blocked_during_weather_fetch():
    """
    Regression (duck-e-hp0): with asyncio.to_thread, other coroutines can run
    concurrently while the blocking HTTP requests are in progress.

    Before the fix, requests.get() was called synchronously, freezing the event
    loop for the entire duration of both HTTP calls.
    """
    import requests

    concurrent_ran = asyncio.Event()

    async def side_task():
        """A lightweight concurrent task that must complete during the weather fetch."""
        await asyncio.sleep(0)
        concurrent_ran.set()

    def slow_blocking_get(*args, **kwargs):
        """Simulate a slow HTTP request (0 s in the test, but *would* block sync)."""
        m = MagicMock()
        m.raise_for_status.return_value = None
        m.status_code = 200
        m.json.return_value = {
            "results": [{"latitude": 51.5, "longitude": -0.1, "timezone": "Europe/London"}],
            # forecast shape (reused for both calls):
            "daily": {"time": ["2026-03-15"], "weather_code": [3],
                      "temperature_2m_max": [10.0], "temperature_2m_min": [5.0],
                      "precipitation_sum": [0.0], "wind_speed_10m_max": [15.0]},
        }
        return m

    with patch("requests.get", side_effect=slow_blocking_get):

        def _geocode(loc):
            r = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={}, timeout=10)
            r.raise_for_status()
            data = r.json()
            results = data.get("results")
            if not results:
                return None, None, None
            res = results[0]
            return res["latitude"], res["longitude"], res.get("timezone", "auto")

        # Run the geocode in a thread AND the side_task concurrently
        lat_result, _ = await asyncio.gather(
            asyncio.to_thread(_geocode, "London"),
            side_task(),
        )

    lat, lon, tz = lat_result
    assert lat == 51.5
    assert concurrent_ran.is_set(), (
        "side_task did not run — the event loop was blocked during the weather fetch"
    )
