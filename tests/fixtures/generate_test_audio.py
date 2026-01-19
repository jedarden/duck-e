#!/usr/bin/env python3
"""
Generate synthetic test audio files for DUCK-E testing.

Usage:
    python tests/fixtures/generate_test_audio.py

Generates:
    - test-speech.wav: Simple tone (simulates voice)
    - silence.wav: Silent audio for mute testing
    - search-query.wav: Tone pattern for search testing
"""

import numpy as np
import os

try:
    from scipy.io import wavfile
except ImportError:
    print("scipy not installed. Run: pip install scipy")
    exit(1)


SAMPLE_RATE = 16000  # 16kHz - standard for voice
FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))


def generate_tone(frequency: float, duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate a sine wave tone."""
    t = np.linspace(0, duration, int(sample_rate * duration), dtype=np.float32)
    samples = np.sin(2 * np.pi * frequency * t)
    # Convert to 16-bit PCM
    return (samples * 32767).astype(np.int16)


def generate_silence(duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate silent audio."""
    return np.zeros(int(sample_rate * duration), dtype=np.int16)


def generate_speech_like(duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate audio that resembles speech patterns (varying tones)."""
    samples = np.array([], dtype=np.int16)

    # Alternate between different frequencies to simulate speech
    frequencies = [200, 300, 250, 400, 350, 200]
    segment_duration = duration / len(frequencies)

    for freq in frequencies:
        segment = generate_tone(freq, segment_duration, sample_rate)
        # Add slight fade in/out for natural sound
        fade_len = min(100, len(segment) // 4)
        segment[:fade_len] = (segment[:fade_len] * np.linspace(0, 1, fade_len)).astype(np.int16)
        segment[-fade_len:] = (segment[-fade_len:] * np.linspace(1, 0, fade_len)).astype(np.int16)
        samples = np.concatenate([samples, segment])

    return samples


def save_wav(filename: str, samples: np.ndarray, sample_rate: int = SAMPLE_RATE):
    """Save samples to WAV file."""
    filepath = os.path.join(FIXTURES_DIR, filename)
    wavfile.write(filepath, sample_rate, samples)
    print(f"Generated: {filepath} ({len(samples) / sample_rate:.2f}s)")


def main():
    print("Generating test audio files...")
    print(f"Output directory: {FIXTURES_DIR}")
    print()

    # 1. Simple tone for basic testing (3 seconds)
    tone = generate_tone(440, 3.0)  # A4 note
    save_wav("test-speech.wav", tone)

    # 2. Silence for mute testing (2 seconds)
    silence = generate_silence(2.0)
    save_wav("silence.wav", silence)

    # 3. Speech-like pattern for search query testing (2 seconds)
    speech = generate_speech_like(2.0)
    save_wav("search-query.wav", speech)

    # 4. Short beep for PTT testing (0.5 seconds)
    beep = generate_tone(800, 0.5)
    save_wav("ptt-beep.wav", beep)

    print()
    print("Done! Audio fixtures ready for testing.")


if __name__ == "__main__":
    main()
