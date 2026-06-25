# Model Identifier Verification (Bead bf-5nj)

**Date**: 2025-01-25

## Verification Result

✅ **CONFIRMED**: `gpt-realtime-2` is the correct OpenAI realtime model identifier.

## Evidence Sources

Official OpenAI documentation verified:
- [Realtime and audio guide](https://developers.openai.com/api/docs/guides/realtime) states: "Build a low-latency voice agent | `gpt-realtime-2` | Voice agents"
- [GPT-Realtime-2 model reference](https://developers.openai.com/api/docs/models/gpt-realtime-2) confirms: "GPT-Realtime-2 is our most capable realtime voice model" with endpoint `/v1/realtime`

## Discrepancy Found and Fixed

**Before**:
- `app/config.py` used: `os.getenv("REALTIME_MODEL", "gpt-4o-realtime-preview")`
- `app/main.py` health check used: `os.getenv("REALTIME_MODEL", "gpt-4o-realtime-preview")`

**After**:
- `app/config.py` now uses: `os.getenv("REALTIME_MODEL", "gpt-realtime-2")`
- `app/main.py` health check now uses: `os.getenv("REALTIME_MODEL", "gpt-realtime-2")`

## Files Updated

1. `app/config.py` - Updated default model from `gpt-4o-realtime-preview` to `gpt-realtime-2`
2. `app/main.py` - Updated health check default model from `gpt-4o-realtime-preview` to `gpt-realtime-2`
3. `CLAUDE.md` - Already correctly documented `gpt-realtime-2` (no change needed)
4. Tests - Already correctly use `gpt-realtime-2` (no change needed)

## Verified Model Details

- **Model name**: `gpt-realtime-2`
- **Endpoint**: `/v1/realtime`
- **Pricing**: $4.00 input / $24.00 output per 1M text tokens
- **Features**: Configurable reasoning effort, 128K context window, speech-to-speech interactions
- **Release**: Generally available (GA), no longer beta
