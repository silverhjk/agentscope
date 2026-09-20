# -*- coding: utf-8 -*-
"""Audio utilities shared across model providers."""
import io
import struct
import wave


def _build_streaming_wav_header(
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Build a 44-byte WAV/RIFF header for streaming PCM.

    The RIFF and ``data`` chunk sizes are set to ``0xFFFFFFFF`` since the
    total length isn't known yet. Decoders that only need sample-rate,
    channel count and bit depth (e.g. the web ``WavStreamPlayer``) treat
    everything after the ``data`` chunk header as PCM, so this is
    sufficient for live decoding of an open-ended stream.

    Both DashScope omni and OpenAI streaming deliver raw PCM upstream;
    prefixing the first chunk with this header lets the frontend start
    playback immediately without buffering the whole response.
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    return (
        b"RIFF"
        + struct.pack("<I", 0xFFFFFFFF)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + struct.pack(
            "<HHIIHH",
            1,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
        )
        + b"data"
        + struct.pack("<I", 0xFFFFFFFF)
    )


def finalize_wav_bytes(
    data: bytes,
    *,
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Return a self-contained WAV DingTalk / ``wave`` can play.

    Streaming TTS often prefixes :func:`_build_streaming_wav_header` (RIFF /
    data sizes ``0xFFFFFFFF``). Clients that require a finished file (DingTalk
    voice bubbles) reject those payloads. This rewrites sizes from the real
    PCM length. Already-valid fixed WAVs are returned unchanged. Bare PCM is
    wrapped with a standard header using the given format params.
    """
    if not data:
        return data

    if len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        riff_size = struct.unpack_from("<I", data, 4)[0]
        # Locate the ``data`` chunk (standard 44-byte header or slightly larger).
        data_offset = data.find(b"data")
        if data_offset < 0 or data_offset + 8 > len(data):
            return data
        declared = struct.unpack_from("<I", data, data_offset + 4)[0]
        pcm = data[data_offset + 8 :]
        # Streaming placeholder or truncated declared size → rewrite.
        if (
            riff_size == 0xFFFFFFFF
            or declared == 0xFFFFFFFF
            or declared != len(pcm)
        ):
            fmt_chunk = data[12:data_offset]
            out = io.BytesIO()
            out.write(b"RIFF")
            out.write(struct.pack("<I", 4 + len(fmt_chunk) + 8 + len(pcm)))
            out.write(b"WAVE")
            out.write(fmt_chunk)
            out.write(b"data")
            out.write(struct.pack("<I", len(pcm)))
            out.write(pcm)
            return out.getvalue()
        return data

    # Raw PCM — wrap as mono/stereo PCM WAV.
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(max(1, bits_per_sample // 8))
        wav.setframerate(sample_rate)
        wav.writeframes(data)
    return buf.getvalue()
