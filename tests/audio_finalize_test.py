# -*- coding: utf-8 -*-
"""Tests for WAV finalize + DingTalk voice duration helpers."""

import io
import struct
import unittest
import wave

from agentscope._utils._audio import (
    _build_streaming_wav_header,
    finalize_wav_bytes,
)
from agentscope.app.channel._dingtalk._openapi import (
    _estimate_audio_duration_seconds,
    _prepare_dingtalk_voice_bytes,
)


def _pcm(frames: int = 24000, rate: int = 24000) -> bytes:
    # 1 second of silence at 16-bit mono.
    return b"\x00\x00" * frames


class TestFinalizeWav(unittest.TestCase):
    def test_rewrites_streaming_header(self) -> None:
        pcm = _pcm(48000)  # 2s @ 24kHz
        streaming = _build_streaming_wav_header(24000, 1, 16) + pcm
        self.assertEqual(struct.unpack_from("<I", streaming, 4)[0], 0xFFFFFFFF)
        fixed = finalize_wav_bytes(streaming)
        self.assertNotEqual(struct.unpack_from("<I", fixed, 4)[0], 0xFFFFFFFF)
        with wave.open(io.BytesIO(fixed), "rb") as wav:
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getframerate(), 24000)
            self.assertEqual(wav.getnframes(), 48000)

    def test_leaves_valid_wav_unchanged(self) -> None:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(_pcm(16000, 16000))
        original = buf.getvalue()
        self.assertEqual(finalize_wav_bytes(original), original)

    def test_wraps_raw_pcm(self) -> None:
        pcm = _pcm(12000)
        fixed = finalize_wav_bytes(pcm, sample_rate=24000)
        with wave.open(io.BytesIO(fixed), "rb") as wav:
            self.assertEqual(wav.getnframes(), 12000)


class TestDingTalkVoicePrepare(unittest.TestCase):
    def test_prepare_streaming_wav_for_upload(self) -> None:
        pcm = _pcm(24000)
        streaming = _build_streaming_wav_header() + pcm
        data, media, suffix = _prepare_dingtalk_voice_bytes(
            streaming,
            "audio/wav",
        )
        self.assertEqual(media, "audio/wav")
        self.assertEqual(suffix, "wav")
        duration = _estimate_audio_duration_seconds(data, media)
        self.assertEqual(duration, 1)

    def test_duration_clamped_under_60(self) -> None:
        # Fake huge WAV by estimating from bytes fallback if needed
        pcm = _pcm(24000 * 120)  # 120s
        data, media, _ = _prepare_dingtalk_voice_bytes(
            _build_streaming_wav_header() + pcm,
            "audio/wav",
        )
        self.assertEqual(_estimate_audio_duration_seconds(data, media), 59)


if __name__ == "__main__":
    unittest.main()
