#!/usr/bin/env python3
"""Live check of the OpenAI Realtime API facts DUCK-E depends on.

Not collected by pytest (no test_ prefix): it calls the real API. Cost is a few
hundred text tokens plus one short audio reply (well under one cent).

Usage — the key travels by reference, never on the command line:

    OPENAI_API_KEY="$(bao-as openbao-v2 bao kv get -field=OPENAI_API_KEY \
        secret/ardenone-cluster/ducke/openai)" python3 tests/live/realtime_api_check.py

Optional: REALTIME_MODEL=gpt-realtime-2.1 to check another model.

Prints one PASS/FAIL line per fact and exits non-zero on any FAIL. It never
prints the API key, a minted ephemeral secret, or a 401 body.
Recorded results: docs/research/realtime-api-verified-2026-11-12.md
"""
import asyncio
import json
import os
import sys

import httpx
import websockets

KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("REALTIME_MODEL", "gpt-realtime-2")
HEADERS = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
failures = 0


def report(ok: bool, label: str, detail: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}{(' | ' + detail) if detail else ''}")


def mint(session: dict) -> tuple[int, dict]:
    """POST /v1/realtime/client_secrets; return (status, echoed session or error)."""
    resp = httpx.post(
        "https://api.openai.com/v1/realtime/client_secrets",
        headers=HEADERS,
        json={"session": {"type": "realtime", "model": MODEL, **session}},
        timeout=30,
    )
    if resp.status_code == 401:
        return 401, {"message": "unauthorized (body suppressed)"}
    data = resp.json()
    if resp.is_success:
        return resp.status_code, data.get("session", {})
    return resp.status_code, data.get("error", {})


def rest_checks() -> None:
    status, session = mint({"audio": {"input": {"transcription": {"model": "whisper-1"}}}})
    default_vad = session.get("audio", {}).get("input", {}).get("turn_detection", {})
    report(status == 200 and default_vad.get("type") == "server_vad",
           "default turn detection is server_vad", json.dumps(default_vad))

    status, session = mint({"audio": {"output": {"voice": "alloy"}, "input": {
        "turn_detection": {"type": "semantic_vad", "eagerness": "auto"},
        "noise_reduction": {"type": "near_field"},
        "transcription": {"model": "gpt-4o-transcribe", "language": "en"},
    }}})
    echoed = session.get("audio", {}).get("input", {}) if status == 200 else session
    report(status == 200, "semantic_vad + near_field + gpt-4o-transcribe(language=en) accepted",
           json.dumps(echoed)[:300])

    status, _ = mint({"audio": {"input": {"noise_reduction": {"type": "far_field"}}}})
    report(status == 200, "noise_reduction far_field accepted")

    status, err = mint({"audio": {"input": {"transcription": {"model": "gpt-4o-transcribe", "language": "en-US"}}}})
    report(status == 400, "transcription language must be a two-letter code (en-US rejected)",
           str(err.get("message", ""))[:80])

    for voice in ("marin", "cedar"):
        status, _ = mint({"audio": {"output": {"voice": voice}}})
        report(status == 200, f"voice {voice} accepted")
    for voice in ("fable", "nova", "onyx"):
        status, _ = mint({"audio": {"output": {"voice": voice}}})
        report(status == 400, f"voice {voice} rejected (TTS-only voice)")


async def ws_checks() -> None:
    url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    auth = {"Authorization": f"Bearer {KEY}"}

    async def until(ws, types, limit=400):
        for _ in range(limit):
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
            if event["type"] in types:
                return event
        return {"type": "limit"}

    async def update(ws, session):
        await ws.send(json.dumps({"type": "session.update", "session": {"type": "realtime", **session}}))
        return await until(ws, ("session.updated", "error"))

    def text_of(done):
        return [c.get("text") for o in done.get("response", {}).get("output", []) for c in o.get("content", [])]

    # Text-only connection: session.update shapes, typed turns, replay.
    async with websockets.connect(url, additional_headers=auth) as ws:
        await until(ws, ("session.created",))
        report((await update(ws, {"output_modalities": ["text"]}))["type"] == "session.updated",
               "session.update output_modalities=['text']")
        report((await update(ws, {"audio": {"input": {"turn_detection": None}}}))["type"] == "session.updated",
               "session.update turn_detection=null (push-to-talk style)")
        flat = await update(ws, {"voice": "ash"})
        report(flat["type"] == "error", "flat session.voice is rejected",
               str(flat.get("error", {}).get("message", ""))[:80])
        report((await update(ws, {"audio": {"output": {"voice": "ash"}}}))["type"] == "session.updated",
               "nested audio.output.voice accepted before any assistant audio")

        for role, content_type, text in (
            ("user", "input_text", "My favourite colour is teal."),
            ("assistant", "output_text", "Got it, teal."),
        ):
            await ws.send(json.dumps({"type": "conversation.item.create", "item": {
                "type": "message", "role": role, "content": [{"type": content_type, "text": text}]}}))
            event = await until(ws, ("conversation.item.added", "error"))
            report(event["type"] == "conversation.item.added", f"replay item {role}/{content_type} accepted")

        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "id": "msg_ducke_client_0001", "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "What is my favourite colour? One word."}]}}))
        event = await until(ws, ("conversation.item.added", "error"))
        report(event.get("item", {}).get("id") == "msg_ducke_client_0001", "client-supplied item id is kept")
        await ws.send(json.dumps({"type": "response.create"}))
        done = await until(ws, ("response.done", "error"))
        report("teal" in " ".join(str(t) for t in text_of(done)).lower(),
               "typed turn answered from replayed context", str(text_of(done)))

        await ws.send(json.dumps({"type": "response.create", "response": {"instructions": "Reply with exactly: OK"}}))
        done = await until(ws, ("response.done", "error"))
        report(text_of(done) == ["OK"], "per-response instructions honoured", str(text_of(done)))

        await ws.send(json.dumps({"type": "response.create"}))
        await ws.send(json.dumps({"type": "response.create"}))
        event = await until(ws, ("error", "response.done"))
        report(event["type"] == "error", "second response.create while one is active is an error")
        await ws.send(json.dumps({"type": "response.cancel"}))
        await until(ws, ("response.done", "error"))

    # Audio connection: the voice locks once the assistant has spoken.
    async with websockets.connect(url, additional_headers=auth) as ws:
        await until(ws, ("session.created",))
        await update(ws, {"audio": {"input": {"turn_detection": None}, "output": {"voice": "alloy"}}})
        await ws.send(json.dumps({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Say only: quack"}]}}))
        await ws.send(json.dumps({"type": "response.create"}))
        await until(ws, ("response.done", "error"))
        locked = await update(ws, {"audio": {"output": {"voice": "cedar"}}})
        report(locked["type"] == "error", "voice cannot change after assistant audio exists",
               str(locked.get("error", {}).get("message", ""))[:90])


def main() -> int:
    if not KEY:
        print("OPENAI_API_KEY is not set; see the usage line in this file's docstring.")
        return 2
    print(f"model: {MODEL}")
    rest_checks()
    asyncio.run(ws_checks())
    print(f"failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
