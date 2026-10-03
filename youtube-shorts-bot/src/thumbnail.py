"""Segmentin en 'dramatik' karesini çıkarır ve Gemini görsel üretim modeliyle thumbnail üretir."""

import io
import os
import subprocess
import time

from PIL import Image

from src.ffmpeg_utils import get_ffmpeg_bin


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "ffmpeg komutu başarısız oldu:\n"
            f"{' '.join(cmd)}\n"
            f"{result.stderr[-2000:]}"
        )
    return result


def extract_dramatic_frame(
    input_path: str,
    start: float,
    end: float,
    output_path: str,
    threshold: float = 0.15,
) -> str:
    """Segment içinde ffmpeg'in sahne değişimi (scene score) metriğine göre en 'hareketli' anı bulup
    o karayı PNG olarak kaydeder. Belirgin bir sahne değişimi bulunamazsa segmentin ortasındaki
    kareye düşer."""
    duration = max(0.5, end - start)
    scores_path = output_path + ".scores.txt"

    scan_cmd = [
        get_ffmpeg_bin(), "-y",
        "-ss", str(start), "-i", input_path, "-t", str(duration),
        "-vf", f"select='gt(scene,{threshold})',metadata=print:file={scores_path}",
        "-fps_mode", "vfr", "-f", "null", "-",
    ]
    _run(scan_cmd)

    best_offset = None
    best_score = -1.0
    pending_time = None

    if os.path.exists(scores_path):
        with open(scores_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line.startswith("frame:"):
                    for tok in line.split():
                        if tok.startswith("pts_time:"):
                            pending_time = float(tok.split(":", 1)[1])
                elif "lavfi.scene_score" in line and pending_time is not None:
                    score = float(line.split("=")[-1])
                    if score > best_score:
                        best_score = score
                        best_offset = pending_time
        os.remove(scores_path)

    if best_offset is None:
        best_offset = duration / 2

    absolute_time = start + best_offset
    frame_cmd = [
        get_ffmpeg_bin(), "-y",
        "-ss", str(absolute_time), "-i", input_path,
        "-vframes", "1", output_path,
    ]
    _run(frame_cmd)
    return output_path


def _extract_image_bytes(response) -> bytes | None:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        parts = getattr(candidate.content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None and inline_data.data:
                return inline_data.data
    return None


def _load_reference_images(paths: list[str] | None) -> list[Image.Image]:
    images = []
    for p in paths or []:
        if p and os.path.exists(p):
            images.append(Image.open(p))
    return images


def _apply_logo(
    image: Image.Image,
    logo_path: str,
    margin_ratio: float = 0.02,
    logo_width_ratio: float = 0.15,
) -> Image.Image:
    logo = Image.open(logo_path).convert("RGBA")
    target_w = int(image.width * logo_width_ratio)
    ratio = target_w / logo.width
    target_h = int(logo.height * ratio)
    logo = logo.resize((target_w, max(1, target_h)))

    base = image.convert("RGBA")
    margin = int(image.width * margin_ratio)
    x = base.width - target_w - margin
    y = base.height - target_h - margin
    base.alpha_composite(logo, (x, y))
    return base.convert("RGB")


def generate_thumbnail(
    frame_path: str,
    output_path: str,
    segment_title: str,
    segment_reason: str,
    channel_name: str,
    style_reference_paths: list[str] | None = None,
    logo_path: str | None = None,
    model_name: str = "gemini-3.1-flash-image",
    max_retries: int = 5,
    base_delay_seconds: int = 20,
) -> str:
    """Referans kareden yola çıkarak dikkat çekici bir YouTube thumbnail'i üretir."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY bulunamadı. .env dosyasına GOOGLE_API_KEY=... satırını ekleyin."
        )

    from google import genai  # lazy import: sadece thumbnail özelliği aktifken gerekli

    client = genai.Client(api_key=api_key)

    base_frame = Image.open(frame_path)
    reference_images = _load_reference_images(style_reference_paths)

    prompt = (
        f"'{channel_name}' adlı bir League of Legends oynanış YouTube kanalı için bu oyun içi "
        "karadan yola çıkarak dikkat çekici, tıklanma oranı yüksek bir YouTube thumbnail'i oluştur.\n"
        f"Segment başlığı: '{segment_title}'.\n"
        f"Bağlam: {segment_reason}.\n\n"
        "Kurallar:\n"
        "- Büyük, kalın, yüksek kontrastlı 2-5 kelimelik merak uyandırıcı bir Türkçe metin overlay "
        "ekle (örnek üsluplar: '1v5 OUTPLAY', 'BUNU YAPMAZ', '?!').\n"
        "- Renkler canlı ve doygun olsun.\n"
        "- Kompozisyon oyuncunun/aksiyonun net görünmesini sağlasın, aşırı kalabalık olmasın.\n"
        "- 16:9 yatay YouTube thumbnail formatında üret."
    )
    if reference_images:
        prompt += (
            "\n- Verilen referans thumbnail örneklerindeki renk paleti, font stili ve kompozisyona "
            "benzer bir görsel dil kullan."
        )

    contents = [prompt, base_frame, *reference_images]

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(model=model_name, contents=contents)
            image_bytes = _extract_image_bytes(response)
            if image_bytes is None:
                raise ValueError("Gemini yanıtında görsel verisi bulunamadı.")

            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            if logo_path and os.path.exists(logo_path):
                image = _apply_logo(image, logo_path)

            image.save(output_path, "PNG")
            return output_path

        except Exception as e:  # noqa: BLE001
            last_error = e
            message = str(e)
            is_rate_limit = "429" in message or "RESOURCE_EXHAUSTED" in message.upper()
            if attempt < max_retries:
                delay = base_delay_seconds * attempt if is_rate_limit else base_delay_seconds
                print(
                    f"[thumbnail] Deneme {attempt}/{max_retries} başarısız oldu ({e}). "
                    f"{delay} saniye bekleyip tekrar denenecek..."
                )
                time.sleep(delay)
            else:
                break

    raise RuntimeError(
        f"Thumbnail üretimi {max_retries} denemeden sonra başarısız oldu: {last_error}"
    )
