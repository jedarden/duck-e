#!/usr/bin/env python3
"""
Test script to verify the correct OpenAI realtime model identifier.

Tests candidate model names against OpenAI's /v1/realtime/sessions endpoint
to determine which is valid.
"""
import os
import sys
import asyncio
import httpx
from datetime import datetime

# Candidate model identifiers to test
CANDIDATE_MODELS = [
    "gpt-realtime-2",
    "gpt-4o-realtime-preview",
    "gpt-4o-realtime-preview-2024-12-17",
    "gpt-realtime",
]

async def test_model(api_key: str, model: str) -> dict:
    """Test if a model identifier is valid."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                "https://api.openai.com/v1/realtime/sessions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "voice": "alloy",
                }
            )

            return {
                "model": model,
                "status": response.status_code,
                "is_success": response.is_success,
                "response": response.json() if response.is_success or response.status_code < 500 else response.text[:500],
                "error": None if response.is_success else f"HTTP {response.status_code}"
            }
        except Exception as e:
            return {
                "model": model,
                "status": -1,
                "is_success": False,
                "response": None,
                "error": str(e)
            }

async def main():
    """Test all candidate models."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)

    print(f"Testing OpenAI realtime model identifiers at {datetime.now().isoformat()}")
    print("=" * 80)

    valid_models = []
    invalid_models = []

    for model in CANDIDATE_MODELS:
        print(f"\nTesting: {model}")
        result = await test_model(api_key, model)

        if result["is_success"]:
            print(f"  ✓ VALID (HTTP {result['status']})")
            valid_models.append(model)
            # Print response details for valid models
            if result["response"]:
                resp = result["response"]
                if isinstance(resp, dict):
                    if "id" in resp:
                        print(f"    Session ID: {resp['id']}")
                    if "model" in resp:
                        print(f"    Confirmed model: {resp['model']}")
                    if "expires_at" in resp:
                        print(f"    Expires at: {resp['expires_at']}")
        else:
            print(f"  ✗ INVALID ({result['error']})")
            invalid_models.append((model, result["error"]))

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if valid_models:
        print("\n✓ VALID models:")
        for model in valid_models:
            print(f"  - {model}")
    else:
        print("\n✗ No valid models found")

    if invalid_models:
        print("\n✗ INVALID models:")
        for model, error in invalid_models:
            print(f"  - {model}: {error}")

    # Exit with error if no valid models found
    if not valid_models:
        print("\nERROR: No valid realtime model identifiers found", file=sys.stderr)
        sys.exit(1)

    # Print the recommended model (first valid one)
    print(f"\nRecommended model: {valid_models[0]}")
    return valid_models[0]

if __name__ == "__main__":
    recommended = asyncio.run(main())
