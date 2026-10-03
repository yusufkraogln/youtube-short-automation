"""ffmpeg ile .ass altyazısını videoya yakar."""

import os
import subprocess

from src.ffmpeg_utils import get_ffmpeg_bin


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg altyazı yakma başarısız oldu:\n"
            f"{' '.join(cmd)}\n"
            f"{result.stderr[-2000:]}"
        )
    return result


def _escape_ass_path(path: str) -> str:
    abs_path = os.path.abspath(path)
    # ffmpeg filtre söz diziminde ':' ayraç anlamına geldiği için kaçış gerekir.
    return abs_path.replace("\\", "\\\\").replace(":", "\\:")


_WATERMARK_POSITIONS = {
    "top_left": ("{margin}", "{margin}"),
    "top_right": ("W-w-{margin}", "{margin}"),
    "bottom_left": ("{margin}", "H-h-{margin}"),
    "bottom_right": ("W-w-{margin}", "H-h-{margin}"),
}


def burn_subtitles(
    input_path: str,
    ass_path: str,
    output_path: str,
    watermark_cfg: dict | None = None,
    fade_cfg: dict | None = None,
    duration: float | None = None,
) -> str:
    """Altyazıyı videoya yakar; watermark_cfg verilip aktifse (ve logo dosyası varsa) köşeye
    isteğe bağlı bir logo bindirir, fade_cfg verilip aktifse klibin başına/sonuna karartma-
    aydınlatma (fade in/out, hem görüntü hem ses) ekler — hepsi aynı ffmpeg geçişinde.

    fade_cfg: {"enabled", "fade_in_seconds", "fade_out_seconds"}. Çıkış geçişinin ne zaman
    başlayacağını hesaplamak için `duration` (klibin saniye cinsinden süresi) gerekir.
    """
    ass_arg = _escape_ass_path(ass_path)
    watermark_cfg = watermark_cfg or {}
    fade_cfg = fade_cfg or {}

    logo_path = watermark_cfg.get("logo_path")
    use_watermark = bool(watermark_cfg.get("enabled")) and bool(logo_path) and os.path.exists(logo_path)

    fade_in = float(fade_cfg.get("fade_in_seconds") or 0) if fade_cfg.get("enabled") else 0.0
    fade_out = float(fade_cfg.get("fade_out_seconds") or 0) if fade_cfg.get("enabled") else 0.0
    use_fade = bool(duration) and (fade_in > 0 or fade_out > 0)

    video_fade_filters = []
    audio_fade_filters = []
    if use_fade:
        if fade_in > 0:
            video_fade_filters.append(f"fade=t=in:st=0:d={fade_in}")
            audio_fade_filters.append(f"afade=t=in:st=0:d={fade_in}")
        if fade_out > 0:
            start = max(0.0, duration - fade_out)
            video_fade_filters.append(f"fade=t=out:st={start}:d={fade_out}")
            audio_fade_filters.append(f"afade=t=out:st={start}:d={fade_out}")

    video_fade_suffix = ("," + ",".join(video_fade_filters)) if video_fade_filters else ""
    audio_fade_chain = ",".join(audio_fade_filters)

    cmd = [get_ffmpeg_bin(), "-y", "-i", input_path]

    if use_watermark:
        width_percent = watermark_cfg.get("width_percent", 18)
        margin_percent = watermark_cfg.get("margin_percent", 4)
        position = watermark_cfg.get("position", "bottom_right")
        x_tpl, y_tpl = _WATERMARK_POSITIONS.get(position, _WATERMARK_POSITIONS["bottom_right"])
        margin_expr = f"(W*{margin_percent / 100})"
        x_expr = x_tpl.format(margin=margin_expr)
        y_expr = y_tpl.format(margin=margin_expr)
        # scale filtresi 2 girdiyle: rw/rh, ikinci (referans) girdinin boyutlarıdır — logo,
        # ana videonun ([subbed]) genişliğine ORANLA büyütülür, overlay de onu köşeye yerleştirir.
        filter_complex = (
            f"[0:v]ass='{ass_arg}'[subbed];"
            f"[1:v][subbed]scale=w=rw*{width_percent / 100}:h=-2[logo];"
            f"[subbed][logo]overlay=x={x_expr}:y={y_expr}{video_fade_suffix}[vout]"
        )
        cmd += ["-i", logo_path]
    else:
        filter_complex = f"[0:v]ass='{ass_arg}'{video_fade_suffix}[vout]"

    audio_map = "0:a"
    if audio_fade_chain:
        filter_complex += f";[0:a]{audio_fade_chain}[aout]"
        audio_map = "[aout]"

    cmd += ["-filter_complex", filter_complex, "-map", "[vout]", "-map", audio_map]

    # afade sesi yeniden kodlamayı gerektirir (copy ile birlikte kullanılamaz).
    audio_codec_args = ["-c:a", "aac", "-b:a", "128k"] if audio_fade_chain else ["-c:a", "copy"]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        *audio_codec_args,
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    return output_path
