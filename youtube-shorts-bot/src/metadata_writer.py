"""Kanıtlanmış (proven) başlıklardan few-shot öğrenerek segment başlık/açıklama/etiket üretimi."""

import json
import os
import time

from PIL import Image

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "description", "tags"],
}


def load_proven_titles(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    titles = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                titles.append(line)
    return titles


def generate_metadata(
    segment_text: str,
    segment_title: str,
    segment_reason: str,
    proven_titles: list[str],
    frame_path: str | None = None,
    model_name: str = "gemini-3.6-flash",
    max_retries: int = 5,
    base_delay_seconds: int = 20,
    similar_videos: list[dict] | None = None,
) -> dict:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY bulunamadı. .env dosyasına GOOGLE_API_KEY=... satırını ekleyin."
        )

    from google import genai  # lazy import: sadece metadata özelliği aktifken gerekli
    from google.genai import types

    client = genai.Client(api_key=api_key)

    examples_block = ""
    if similar_videos:
        examples = "\n".join(f"- {v['title']} (izlenme: {v['view_count']:,})".replace(",", ".") for v in similar_videos[:15])
        examples_block = (
            "YouTube'da bu konuya benzer, gerçekten popüler videoların başlıkları (yeni başlığı bu "
            f"üslup ve kalıpta üret, birebir kopyalama):\n{examples}\n\n"
        )
        all_tags = [t for v in similar_videos for t in v.get("tags", [])]
        if all_tags:
            from collections import Counter

            top_tags = [t for t, _ in Counter(all_tags).most_common(20)]
            examples_block += (
                "Bu konudaki popüler videolarda gerçekten kullanılan etiketler (uygun olanları "
                f"kendi etiketlerine dahil et):\n{', '.join(top_tags)}\n\n"
            )
    elif proven_titles:
        examples = "\n".join(f"- {t}" for t in proven_titles[:30])
        examples_block = (
            "Kanalda geçmişte gerçekten iyi performans göstermiş başlık örnekleri (yeni başlığı "
            f"bu üslup ve kalıpta üret, birebir kopyalama):\n{examples}\n\n"
        )

    prompt = (
        "Bir League of Legends oynanış YouTube Shorts kanalı için bu klip segmentine SEO odaklı "
        "başlık, açıklama ve etiketler üret.\n\n"
        f"{examples_block}"
        f"Segment önerilen başlığı: {segment_title}\n"
        f"Segment gerekçesi: {segment_reason}\n"
        f"Segment transkripti: {segment_text}\n\n"
        "Kurallar:\n"
        "- title: kanalın geçmiş başarılı başlıklarına üslup olarak benzeyen, tıklanma oranı "
        "yüksek, Türkçe bir başlık (en fazla ~100 karakter).\n"
        "- description: 2-4 cümlelik açıklama ve sonunda birkaç alakalı hashtag.\n"
        "- tags: şampiyon adı, rol (top/jungle/mid/adc/support), oyun modu gibi League of Legends'a "
        "özgü SEO etiketleri dahil, 8-15 adet etiket listesi. Ekteki görselde bir şampiyon/HUD/kill "
        "feed görüyorsan bu bilgiyi etiketlere yansıt."
    )

    contents: list = [prompt]
    if frame_path and os.path.exists(frame_path):
        contents.append(Image.open(frame_path))

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.8,
                ),
            )
            data = json.loads(response.text)
            title = str(data["title"]).strip()
            description = str(data["description"]).strip()
            tags = [str(t).strip() for t in data.get("tags", []) if str(t).strip()]

            if not title or not tags:
                raise ValueError("Gemini eksik metadata döndürdü.")

            return {"title": title, "description": description, "tags": tags}

        except Exception as e:  # noqa: BLE001
            last_error = e
            message = str(e)
            is_rate_limit = "429" in message or "RESOURCE_EXHAUSTED" in message.upper()
            if attempt < max_retries:
                delay = base_delay_seconds * attempt if is_rate_limit else base_delay_seconds
                print(
                    f"[metadata_writer] Deneme {attempt}/{max_retries} başarısız oldu ({e}). "
                    f"{delay} saniye bekleyip tekrar denenecek..."
                )
                time.sleep(delay)
            else:
                break

    raise RuntimeError(
        f"Metadata üretimi {max_retries} denemeden sonra başarısız oldu: {last_error}"
    )


def save_aggregated_metadata(entries: list[dict], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
