"""Kullanılacak ffmpeg ikili dosyasını bulur.

Standart Homebrew `ffmpeg` formülü libass içermez (altyazı yakma çalışmaz). Bu proje
altyazı yakmak (`ass` filtresi) için libass gerektirir, bu yüzden mevcutsa
`ffmpeg-full` (libass destekli) sürümünü tercih eder.
"""

import os
import shutil

_CACHED_BIN: str | None = None
_CACHED_PROBE_BIN: str | None = None

_CANDIDATE_PATHS = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",  # Apple Silicon Homebrew (keg-only)
    "/usr/local/opt/ffmpeg-full/bin/ffmpeg",  # Intel Homebrew (keg-only)
]

_CANDIDATE_PROBE_PATHS = [
    "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe",
    "/usr/local/opt/ffmpeg-full/bin/ffprobe",
]


def get_ffmpeg_bin() -> str:
    global _CACHED_BIN
    if _CACHED_BIN:
        return _CACHED_BIN

    env_override = os.environ.get("FFMPEG_BINARY")
    if env_override and shutil.which(env_override):
        _CACHED_BIN = env_override
        return _CACHED_BIN

    for path in _CANDIDATE_PATHS:
        if os.path.exists(path):
            _CACHED_BIN = path
            return _CACHED_BIN

    fallback = shutil.which("ffmpeg")
    if not fallback:
        raise RuntimeError(
            "ffmpeg bulunamadı. Kurulum için README.md dosyasına bakın "
            "(macOS: `brew install ffmpeg-full`)."
        )
    _CACHED_BIN = fallback
    return _CACHED_BIN


def get_ffprobe_bin() -> str:
    global _CACHED_PROBE_BIN
    if _CACHED_PROBE_BIN:
        return _CACHED_PROBE_BIN

    env_override = os.environ.get("FFPROBE_BINARY")
    if env_override and shutil.which(env_override):
        _CACHED_PROBE_BIN = env_override
        return _CACHED_PROBE_BIN

    for path in _CANDIDATE_PROBE_PATHS:
        if os.path.exists(path):
            _CACHED_PROBE_BIN = path
            return _CACHED_PROBE_BIN

    fallback = shutil.which("ffprobe")
    if not fallback:
        raise RuntimeError(
            "ffprobe bulunamadı. Kurulum için README.md dosyasına bakın "
            "(macOS: `brew install ffmpeg-full`)."
        )
    _CACHED_PROBE_BIN = fallback
    return _CACHED_PROBE_BIN
