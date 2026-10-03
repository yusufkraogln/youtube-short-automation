"""yt-dlp sarmalayıcı: verilen YouTube URL'sini en yüksek kalitede mp4 olarak indirir."""

import glob
import os
import re
import tempfile
import time

import yt_dlp


def download_video(url: str, output_dir: str = "downloads") -> dict:
    """Videoyu indirir ve {"path", "video_id", "title", "duration"} döndürür."""
    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise RuntimeError(f"Video indirilemedi: {e}") from e

    if info is None:
        raise RuntimeError("yt-dlp video bilgisi döndürmedi.")

    video_id = info["id"]
    title = info.get("title", video_id)
    duration = info.get("duration")

    filepath = ydl.prepare_filename(info)
    base, _ = os.path.splitext(filepath)
    mp4_path = base + ".mp4"
    if os.path.exists(mp4_path):
        filepath = mp4_path

    if not os.path.exists(filepath):
        raise RuntimeError(f"İndirilen dosya bulunamadı: {filepath}")

    return {
        "path": filepath,
        "video_id": video_id,
        "title": title,
        "duration": duration,
    }


def download_audio_only(url: str, output_dir: str = "downloads") -> dict:
    """Sadece ses akışını indirir (tam video indirmekten çok daha hafif/hızlı) — yalnızca
    transkript gerektiren işler (örn. eski video SEO önerisi) için kullanılır."""
    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(output_dir, "%(id)s_audio.%(ext)s"),
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as e:
        raise RuntimeError(f"Ses indirilemedi: {e}") from e

    if info is None:
        raise RuntimeError("yt-dlp video bilgisi döndürmedi.")

    filepath = ydl.prepare_filename(info)
    if not os.path.exists(filepath):
        raise RuntimeError(f"İndirilen ses dosyası bulunamadı: {filepath}")

    return {
        "path": filepath,
        "video_id": info["id"],
        "title": info.get("title", info["id"]),
    }


def _vtt_to_text(path: str) -> str:
    """YouTube'un ürettiği .vtt altyazı dosyasını düz metne çevirir (zaman damgaları, stil
    etiketleri ve otomatik altyazılara özgü tekrar eden satırlar temizlenir)."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return " ".join(lines)


def fetch_captions_text(
    url: str, langs: list[str] | None = None, lang_retry_delay: float = 1.5
) -> dict | None:
    """YouTube'un kendi altyazılarını (varsa insan yapımı, yoksa otomatik) indirip düz metne
    çevirir — hiçbir video/ses dosyası indirmediği ve Whisper çalıştırmadığı için ses indirip
    transkript çıkarmaktan çok daha hızlıdır. Altyazı bulunamazsa None döner (çağıran taraf
    ses indirip Whisper'a düşmeli).

    Diller TEK TEK (art arda, aralarında kısa bir bekleme ile) denenir — aynı anda birden çok
    dil istemek YouTube'un altyazı ucunda rate-limit (429) riskini artırıyor."""
    langs = langs or ["tr", "en"]

    for i, lang in enumerate(langs):
        if i > 0:
            time.sleep(lang_retry_delay)

        with tempfile.TemporaryDirectory() as tmpdir:
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [lang],
                "subtitlesformat": "vtt",
                "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError:
                info = None

            vtt_files = sorted(glob.glob(os.path.join(tmpdir, "*.vtt")))
            if not vtt_files:
                continue

            text = _vtt_to_text(vtt_files[0])
            if not text:
                continue

            title = (info or {}).get("title", "")
            if not title:
                # Altyazı indirme kısmen başarısız olduysa info de kaybolmuş olabilir —
                # başlığı ayrı, hafif bir metadata-only çağrıyla almayı dene.
                try:
                    with yt_dlp.YoutubeDL({"skip_download": True, "quiet": True, "no_warnings": True}) as ydl2:
                        meta = ydl2.extract_info(url, download=False)
                    title = (meta or {}).get("title", "")
                except yt_dlp.utils.DownloadError:
                    pass

            return {"text": text, "title": title}

    return None
