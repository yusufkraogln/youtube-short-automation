"""Gemini API ile transkript üzerinden en 'viral olabilecek' anların tespiti."""

import json
import os
import time

from google import genai
from google.genai import types

SYSTEM_PROMPT = """Sen kısa video (YouTube Shorts / TikTok / Instagram Reels) içerik editörü olarak \
çalışan bir uzmansın. Sana zaman damgalı bir video transkripti verilecek. Görevin, bu videodan \
bağımsız kısa videolar (shorts) haline getirilebilecek en 'viral olma potansiyeli yüksek' \
anları tespit etmek.

Bir segmenti seçerken şu kriterlere göre değerlendir:
1. Güçlü açılış: Segmentin ilk 2-3 saniyesi izleyicinin dikkatini hemen çekmeli (güçlü bir cümle,
   şaşırtıcı bir iddia, soru ya da net bir "hook").
2. Bağımsızlık: Segment, video geri kalanı izlenmeden de tek başına anlaşılabilir ve anlamlı olmalı.
   Ortasında başlayıp biten, bağlam gerektiren parçalar seçme.
3. Duygusal yoğunluk / ilgi çekicilik: Sürpriz, tartışmalı bir fikir, komik bir an, duygusal bir
   itiraf ya da net bir "aha" / içgörü anı içermeli.
4. Süre: İdeal olarak belirtilen min-max saniye aralığında olmalı, en fazla 90 saniye.
5. Cümle bütünlüğü: Segment, bir cümlenin ortasında başlamamalı veya bitmemeli; doğal başlangıç ve
   bitiş noktaları seç (transkriptteki zaman damgalarına sadık kal).

Yalnızca istenen JSON şemasına uygun bir çıktı üret. Başka hiçbir açıklama, markdown ya da metin ekleme.
Her segment için:
- "start" ve "end": transkriptteki zaman damgalarından alınmış saniye cinsinden değerler (sayı).
- "title": izleyiciyi tıklamaya teşvik edecek kısa, çarpıcı bir başlık önerisi (transkriptle aynı dilde).
- "reason": bu segmentin neden viral olabileceğine dair 1-2 cümlelik kısa gerekçe (transkriptle aynı dilde).
- "score": 0 ile 10 arasında viral potansiyel skoru (10 = en yüksek potansiyel).

Segmentler birbiriyle çakışmamalı ve videonun farklı bölümlerinden seçilmiş olmalı."""


def _extract_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def _build_transcript_text(segments: list[dict]) -> str:
    lines = [f"[{seg['start']:.2f} - {seg['end']:.2f}] {seg['text']}" for seg in segments]
    return "\n".join(lines)


RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "start": {"type": "number"},
            "end": {"type": "number"},
            "title": {"type": "string"},
            "reason": {"type": "string"},
            "score": {"type": "number"},
        },
        "required": ["start", "end", "title", "reason", "score"],
    },
}


def analyze_transcript(
    segments: list[dict],
    video_duration: float,
    model_name: str = "gemini-3.6-flash",
    min_duration: int = 20,
    max_duration: int = 60,
    num_candidates: int = 8,
    max_retries: int = 5,
    base_delay_seconds: int = 20,
) -> list[dict]:
    """Transkripti Gemini'ye gönderir, doğrulanmış aday segment listesini döndürür."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY bulunamadı. .env dosyasına GOOGLE_API_KEY=... satırını ekleyin."
        )

    if not segments:
        raise RuntimeError("Analiz için transkript segmenti bulunamadı.")

    client = genai.Client(api_key=api_key)
    transcript_text = _build_transcript_text(segments)

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Video toplam süresi: {video_duration:.1f} saniye.\n"
        f"İdeal segment süresi: {min_duration}-{max_duration} saniye (maksimum 90 saniye).\n"
        f"En fazla {num_candidates} aday segment döndür.\n\n"
        f"Zaman damgalı transkript:\n{transcript_text}\n"
    )

    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.7,
                ),
            )
            raw_text = _extract_json_text(response.text)
            data = json.loads(raw_text)

            if not isinstance(data, list) or len(data) == 0:
                raise ValueError("Gemini boş ya da geçersiz bir liste döndürdü.")

            validated = []
            for item in data:
                try:
                    start = float(item["start"])
                    end = float(item["end"])
                    if end <= start:
                        continue
                    end = min(end, video_duration)
                    start = max(start, 0.0)
                    if end - start < 3:
                        continue
                    validated.append(
                        {
                            "start": round(start, 2),
                            "end": round(end, 2),
                            "title": str(item.get("title", "Untitled")).strip(),
                            "reason": str(item.get("reason", "")).strip(),
                            "score": float(item.get("score", 0)),
                        }
                    )
                except (KeyError, TypeError, ValueError):
                    continue

            if not validated:
                raise ValueError("Gemini yanıtında geçerli hiçbir segment bulunamadı.")

            return validated

        except Exception as e:  # noqa: BLE001 - LLM/HTTP hatalarını tek noktadan yönetiyoruz
            last_error = e
            message = str(e)
            is_rate_limit = "429" in message or "RESOURCE_EXHAUSTED" in message.upper()

            if attempt < max_retries:
                delay = base_delay_seconds * attempt if is_rate_limit else base_delay_seconds
                print(
                    f"[analyzer] Deneme {attempt}/{max_retries} başarısız oldu ({e}). "
                    f"{delay} saniye bekleyip tekrar denenecek..."
                )
                time.sleep(delay)
            else:
                break

    raise RuntimeError(
        f"Gemini analizi {max_retries} denemeden sonra başarısız oldu: {last_error}"
    )
