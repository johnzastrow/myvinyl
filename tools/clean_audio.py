# /// script
# requires-python = ">=3.11"
# dependencies = ["av>=14", "numpy"]
# ///
"""Remove mains hum and normalize voice level in an OBS recording, leaving the video untouched.

Usage:
    uv run tools/clean_audio.py "C:/Users/br8kw/Videos/OBS/2026-10-11 11-21-33.mp4" [more files...]

Writes "<name>-clean.mp4" next to each input. The video stream is copied bit-for-bit;
only the audio is filtered and re-encoded (AAC 192 kbps).

Filter chain (ffmpeg filters, via PyAV's bundled libavfilter):
    1. high-pass 85 Hz x2     - removes the 60 Hz hum fundamental and low rumble
    2. notches at 60..420 Hz  - removes the hum harmonics that sit inside the voice range
    3. loudnorm -16 LUFS      - brings quiet headset speech up to YouTube-friendly loudness,
                                true peak capped at -1.5 dBTP
"""

import argparse
import sys
from pathlib import Path

import av
import numpy as np

HUM_HZ = 60  # North American mains; use 50 in Europe/most of Asia
HARMONICS = range(1, 8)  # 60, 120, ... 420 Hz
SAMPLE_RATE = 48000


def build_graph(in_stream: av.audio.stream.AudioStream) -> av.filter.Graph:
    graph = av.filter.Graph()
    # Two cascaded 2-pole high-passes = 24 dB/octave: buries 60 Hz without thinning the voice.
    chain = [
        graph.add_abuffer(template=in_stream),
        graph.add("highpass", "f=85:poles=2"),
        graph.add("highpass", "f=85:poles=2"),
    ]
    for n in HARMONICS:
        # Narrow notches (Q 25) so the voice around each hum line is kept.
        chain.append(graph.add("bandreject", f"f={HUM_HZ * n}:width_type=q:w=25"))
    chain += [
        graph.add("loudnorm", "I=-16:TP=-1.5:LRA=11"),
        graph.add("aresample", str(SAMPLE_RATE)),  # loudnorm upsamples internally
        graph.add("aformat", "sample_fmts=fltp:channel_layouts=stereo"),
        graph.add("abuffersink"),
    ]
    for a, b in zip(chain, chain[1:], strict=False):
        a.link_to(b)
    graph.configure()
    return graph


def hum_ratio_db(samples: np.ndarray) -> float:
    """Energy at 60/180/300 Hz relative to the 100-4000 Hz voice band (lower is better)."""
    n = 1 << 15
    segs = samples[: len(samples) // n * n].reshape(-1, n)
    if not len(segs):
        return float("nan")
    spec = np.mean([np.abs(np.fft.rfft(s * np.hanning(n))) ** 2 for s in segs], 0)
    f = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
    hum = sum(spec[np.abs(f - h) < 2].sum() for h in (HUM_HZ, 3 * HUM_HZ, 5 * HUM_HZ))
    voice = spec[(f > 100) & (f < 4000)].sum()
    return 10 * np.log10(hum / voice + 1e-20)


def clean(src: Path) -> Path:
    dst = src.with_name(f"{src.stem}-clean{src.suffix}")
    before, after = [], []
    with av.open(str(src)) as inp, av.open(str(dst), "w") as out:
        in_v = next(iter(inp.streams.video), None)
        in_a = inp.streams.audio[0]
        out_v = out.add_stream_from_template(in_v) if in_v else None
        out_a = out.add_stream("aac", rate=SAMPLE_RATE, layout="stereo")
        out_a.bit_rate = 192_000
        graph = build_graph(in_a)

        def drain():
            while True:
                try:
                    frame = graph.pull()
                except (BlockingIOError, av.error.EOFError):
                    return
                after.append(frame.to_ndarray().mean(axis=0))
                frame.pts = None  # let the encoder assign timestamps
                out.mux(out_a.encode(frame))

        streams = [s for s in (in_v, in_a) if s is not None]
        for packet in inp.demux(*streams):
            if packet.stream is in_v:
                if packet.dts is None:  # demuxer flush packet
                    continue
                packet.stream = out_v
                out.mux(packet)
                continue
            for frame in packet.decode():
                before.append(frame.to_ndarray().mean(axis=0))
                graph.push(frame)
                drain()
        graph.push(None)  # flush the filters
        drain()
        out.mux(out_a.encode(None))

    b, a = np.concatenate(before), np.concatenate(after)
    print(f"{src.name} -> {dst.name}")
    print(f"  hum vs voice: {hum_ratio_db(b):6.1f} dB -> {hum_ratio_db(a):6.1f} dB")
    print(
        f"  RMS level:    {20 * np.log10(np.sqrt((b**2).mean())):6.1f} dBFS -> "
        f"{20 * np.log10(np.sqrt((a**2).mean())):6.1f} dBFS"
    )
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", type=Path)
    args = ap.parse_args()
    failed = 0
    for f in args.files:
        if not f.is_file() or f.stem.endswith("-clean"):
            print(f"skipping {f}", file=sys.stderr)
            continue
        try:
            clean(f)
        except Exception as e:  # keep going on a bad file, report it
            print(f"FAILED {f}: {e}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
