"""ffmpeg (subprocess) ile: segment kırpma + 16:9 -> 9:16 dikey dönüşüm."""

import os
import subprocess

from src.ffmpeg_utils import get_ffmpeg_bin, get_ffprobe_bin


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg komutu başarısız oldu:\n"
            f"{' '.join(cmd)}\n"
            f"{result.stderr[-2000:]}"
        )
    return result


def _probe_duration(path: str) -> float | None:
    """Bir video dosyasının gerçek süresini (saniye) döndürür, okunamazsa None."""
    result = subprocess.run(
        [get_ffprobe_bin(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def _build_zoom_expr(zoom_start: float, duration_seconds: float) -> str:
    """t=0'da zoom_start kadar yakınlaşmış, t=duration_seconds'ta 1.0'a (normal) yumuşakça
    dönen bir zoom çarpanı ifadesi üretir. min(t/D,1) sayesinde D saniyeden sonra otomatik 1'de
    sabitlenir, ayrı bir koşul (if/lt) gerekmez."""
    return f"({zoom_start}-({zoom_start}-1)*min(t/{duration_seconds}\\,1))"


def _offset_x_expr(crop_w_expr: str, x_offset: float) -> str:
    """Kadrajın kaynak görüntü içindeki yatay konumu için crop filtresinin x parametresini üretir.
    x_offset=0 -> tam ortalanmış (ffmpeg'in varsayılan merkezleme formülüyle aynı), -1 -> tam sol
    kenar, 1 -> tam sağ kenar."""
    x_offset = max(-1.0, min(1.0, x_offset))
    return f"(iw-({crop_w_expr}))/2*(1+{x_offset})"


def _zoom_crop_stage(width: int, height: int, zoom_cfg: dict) -> str:
    """Giriş 'zoom punch' efekti: kısa bir süre için yakın çekimden normale yumuşakça açılan
    zaman-değişkenli bir crop+scale aşaması döndürür (zoom kapalıysa boş string)."""
    if not zoom_cfg.get("enabled"):
        return ""
    zoom_start = zoom_cfg.get("zoom_start", 1.15)
    duration_seconds = zoom_cfg.get("duration_seconds", 0.5)
    expr = _build_zoom_expr(zoom_start, duration_seconds)
    return (
        f",crop='iw/{expr}':'ih/{expr}':'(iw-iw/{expr})/2':'(ih-ih/{expr})/2',"
        f"scale={width}:{height}"
    )


def _even(value: float) -> int:
    """libx264 çift genişlik/yükseklik istiyor; en yakın çift sayıya yuvarlar (en az 2)."""
    return max(2, int(round(value / 2) * 2))


def cut_and_verticalize(
    input_path: str,
    start: float,
    end: float,
    output_path: str,
    width: int = 1080,
    height: int = 1920,
    mode: str = "blur_pad",
    zoom_config: dict | None = None,
    x_offset: float = 0.0,
    scale: float = 1.0,
) -> str:
    """Videodan [start, end] aralığını keser ve 9:16 dikey formata çevirir.

    zoom_config (opsiyonel): {"enabled", "zoom_start", "duration_seconds", "sound_effect_path"}
    verilirse klibin başına kısa bir zoom-out "giriş vuruşu" efekti (ve varsa eşlik eden bir ses
    efekti) eklenir.
    x_offset: kadrajın kaynak görüntü içindeki yatay konumu (-1 tam sol, 0 orta, 1 tam sağ) —
    ana konu kadrajın merkezinde değilse kullanılır.
    scale: ön plan videosunun kalıcı büyütme/küçültme çarpanı (1.0 = normal). blur_pad modunda
    1'den büyük değerler kadrajı sıkılaştırıp (zoom in) taşan kısımları otomatik kırpar, 1'den
    küçük değerler ön planı küçültüp etrafında daha fazla bulanık arka plan gösterir.
    """
    duration = max(0.1, end - start)
    zoom_cfg = zoom_config or {}
    scale = max(0.3, min(3.0, scale or 1.0))

    if mode == "center_crop":
        # center_crop'ta arka plan yok, bu yüzden büyütme/küçültme kırpma penceresini
        # değiştirerek yapılır — küçültme (scale<1) kaynağın boyutunu aşacağından güvenli
        # şekilde 1.0'da (etkisiz) sınırlanır, sadece büyütme (zoom in) tam çalışır.
        effective_scale = max(1.0, scale)
        zoom_stage = _zoom_crop_stage(width, height, zoom_cfg)
        crop_w_expr = f"(ih*{width}/{height})/{effective_scale}"
        crop_h_expr = f"ih/{effective_scale}"
        x_expr = _offset_x_expr(crop_w_expr, x_offset)
        y_expr = f"(ih-({crop_h_expr}))/2"
        vf = f"crop={crop_w_expr}:{crop_h_expr}:x={x_expr}:y={y_expr},scale={width}:{height}{zoom_stage},setsar=1"
    else:  # blur_pad (varsayılan)
        # Ön plan videosu orijinal 16:9 oranında bırakılmıyor; ortadan kareye kırpılıp
        # tam genişliğe (width x width) büyütülüyor — böylece ekranın çok daha büyük bir
        # kısmını kaplıyor (16:9 bırakılsaydı yükseklik sadece ~width*9/16 olurdu).
        # scale, bu karenin son boyutunu değiştirir: 1'den büyükse kare kanvastan taşar ve
        # overlay onu ortalarken otomatik kırpar (zoom in); 1'den küçükse kare kanvastan
        # küçük kalır ve etrafında bulanık arka plan görünür (zoom out).
        fg_size = _even(width * scale)
        fg_zoom_stage = _zoom_crop_stage(fg_size, fg_size, zoom_cfg)
        fg_x_expr = _offset_x_expr("ih", x_offset)
        vf = (
            "split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=20[bg2];"
            f"[fg]crop=ih:ih:x={fg_x_expr}:y=0,scale={fg_size}:{fg_size}{fg_zoom_stage}[fg2];"
            "[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )

    sfx_path = zoom_cfg.get("sound_effect_path") if zoom_cfg.get("enabled") else None
    has_sfx = bool(sfx_path) and os.path.exists(sfx_path)

    cmd = [get_ffmpeg_bin(), "-y", "-ss", str(start), "-i", input_path]

    if has_sfx:
        # Orijinal ses korunur, üzerine giriş ses efekti karıştırılır (amix, orijinali baskın tutacak
        # ağırlıklarla). "duration=first" -> nihai süre orijinal sesin (0. girdi) süresine eşitlenir,
        # daha kısa olan efekt sesi otomatik sessizlikle tamamlanır.
        cmd += ["-i", sfx_path]
        filter_complex = f"[0:v]{vf}[vout];[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.7[aout]"
        cmd += ["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"]
    else:
        cmd += ["-vf", vf]

    # -t burada bilerek çıktı (output) seçeneği olarak, tüm girdiler/filtreler tanımlandıktan
    # sonra veriliyor: ikinci bir girdi (ses efekti) varken -t'nin yanlış girdiye bağlanmasını
    # (ve süreyi orijinal tam video uzunluğuna geri döndürmesini) önler.
    cmd += [
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)

    # ffmpeg, -ss ile istenen aralık kaynak videonun gerçek uzunluğunu aştığında (örn. kaynak
    # dosya sonradan değiştirilip kısaltıldıysa) yine de "başarılı" (exit code 0) dönebilir ama
    # neredeyse boş bir çıktı üretir. Bunu burada yakalamazsak, bu bozuk dosya sonraki adımlarda
    # (altyazı yakma vb.) anlaşılmaz bir ffmpeg hatasıyla patlar — üstelik varsa önceki geçerli
    # ham dosyanın üzerine yazılmış olur. Süreyi doğrulayıp erkenden net bir hata veriyoruz.
    actual_duration = _probe_duration(output_path)
    if actual_duration is None or actual_duration < duration * 0.5:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise RuntimeError(
            f"Üretilen klip beklenenden çok kısa çıktı ({actual_duration} sn / beklenen {duration:.1f} sn). "
            f"Muhtemelen kaynak video değişti/kısaldı: {input_path}"
        )

    return output_path


def trim_video(input_path: str, start: float, end: float, output_path: str) -> str:
    """Zaten işlenmiş (dikeye çevrilmiş) bir klibi ek filtre uygulamadan [start, end] aralığına
    kırpar. GUI'deki süre kırpma sürgüsü tarafından kullanılır."""
    duration = max(0.1, end - start)
    cmd = [
        get_ffmpeg_bin(), "-y",
        "-ss", str(start),
        "-i", input_path,
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    _run(cmd)
    return output_path
