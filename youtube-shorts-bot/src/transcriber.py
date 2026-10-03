"""faster-whisper sarmalayıcı: video/ses dosyasından kelime bazlı zaman damgalı transkript üretir."""

import difflib
import json
import re

from faster_whisper import WhisperModel

CORRECTION_RE = re.compile(
    r'^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*:\s*doğrusu\s*["\'](.+)["\']\s*$',
    re.IGNORECASE,
)


def transcribe(
    media_path: str,
    model_size: str = "medium",
    language: str | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
) -> dict:
    """Transkripti {"language", "duration", "segments": [...], "words": [...]}  olarak döndürür.

    - segments: whisper'ın doğal cümle/segment bölünmesi (Gemini analizine gönderilir)
    - words: tüm video boyunca düz kelime listesi (altyazı üretiminde kullanılır)
    """
    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as e:
        raise RuntimeError(f"Whisper modeli yüklenemedi ({model_size}): {e}") from e

    try:
        segments_gen, info = model.transcribe(
            media_path,
            word_timestamps=True,
            language=language,
            vad_filter=True,
        )
    except Exception as e:
        raise RuntimeError(f"Transkript çıkarma başarısız oldu: {e}") from e

    segments = []
    words = []

    for seg in segments_gen:
        seg_words = []
        if seg.words:
            for w in seg.words:
                word_entry = {
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                }
                if word_entry["word"]:
                    words.append(word_entry)
                    seg_words.append(word_entry)
        text = seg.text.strip()
        if text:
            segments.append(
                {
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": text,
                    "words": seg_words,
                }
            )

    if not words:
        raise RuntimeError(
            "Transkript boş döndü. Video sessiz olabilir, ses parçası eksik olabilir "
            "ya da konuşma tanınamadı."
        )

    duration = segments[-1]["end"] if segments else 0.0

    return {
        "language": info.language,
        "duration": duration,
        "segments": segments,
        "words": words,
    }


def save_transcript(transcript: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, ensure_ascii=False, indent=2)


def load_transcript(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_correction_line(line: str) -> dict | None:
    """'12.4-12.8: doğrusu "Ahmet"' -> {"start": 12.4, "end": 12.8, "word": "Ahmet"}"""
    match = CORRECTION_RE.match(line)
    if not match:
        return None
    start, end, word = match.groups()
    start, end = float(start), float(end)
    if end <= start:
        return None
    return {"start": start, "end": end, "word": word.strip()}


def apply_corrections(words: list[dict], corrections: list[dict]) -> list[dict]:
    """Her düzeltme, [start, end] aralığıyla çakışan kelimeleri tek bir düzeltilmiş kelimeyle
    değiştirir (aralık birden fazla orijinal kelimeye denk geliyorsa hepsi birleştirilir)."""
    result = list(words)
    for corr in corrections:
        c_start, c_end = corr["start"], corr["end"]
        kept = [w for w in result if w["end"] <= c_start or w["start"] >= c_end]
        new_word = {"word": corr["word"], "start": c_start, "end": c_end}
        kept.append(new_word)
        kept.sort(key=lambda w: w["start"])
        result = kept
    return result


def words_to_text(words: list[dict]) -> str:
    return " ".join(w["word"] for w in words)


def apply_text_diff(words: list[dict], edited_text: str) -> list[dict]:
    """Kullanıcının serbestçe düzenlediği düz metni orijinal kelime zaman damgalarıyla hizalar.

    Değişmeyen kelimeler zamanlarını korur. Değiştirilen/silinen kelimeler, kapsadıkları orijinal
    zaman aralığına eşit olarak paylaştırılır (silinen kelime altyazıdan kaybolur). Yeni eklenen
    kelimeler, komşu kelimeler arasındaki boşluğa (ya da kenarsa küçük bir aralığa) paylaştırılır —
    whisper'ın kendisi bu kelimeleri duymadığı için zamanlaması tahminidir.
    """
    old_tokens = [w["word"] for w in words]
    new_tokens = edited_text.split()

    matcher = difflib.SequenceMatcher(a=old_tokens, b=new_tokens, autojunk=False)
    result: list[dict] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            result.extend(words[i1:i2])
            continue

        new_words_text = new_tokens[j1:j2]
        if not new_words_text:
            continue  # silme: bu kelimeler altyazıdan kaldırılır

        if tag in ("replace", "delete"):
            start = words[i1]["start"]
            end = words[i2 - 1]["end"]
        else:  # insert
            start = words[i1 - 1]["end"] if i1 > 0 else (words[i1]["start"] if i1 < len(words) else 0.0)
            end = words[i1]["start"] if i1 < len(words) else start + 0.4 * len(new_words_text)

        span = max(end - start, 0.05 * len(new_words_text))
        step = span / len(new_words_text)
        for k, tok in enumerate(new_words_text):
            result.append(
                {
                    "word": tok,
                    "start": round(start + k * step, 3),
                    "end": round(start + (k + 1) * step, 3),
                }
            )

    return result


LINE_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*:\s*(.*)$')


def words_to_timed_lines(groups: list[list[dict]]) -> str:
    """Her kelime öbeğini (build_ass'ta ekrana aynı anda çıkan grup) kendi zaman aralığıyla
    birlikte bir satır olarak yazar: '0.00-1.03: Merhaba ben enes'."""
    lines = []
    for g in groups:
        start = g[0]["start"]
        end = g[-1]["end"]
        text = " ".join(w["word"] for w in g)
        lines.append(f"{start:.2f}-{end:.2f}: {text}")
    return "\n".join(lines)


def parse_timed_lines(text: str) -> list[dict] | None:
    """'0.00-1.03: Merhaba ben enes' satırlarını [{"start","end","text"}, ...] olarak ayrıştırır.
    Herhangi bir satır formata uymuyorsa None döner (boş satırlar yok sayılır)."""
    result = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = LINE_RE.match(line)
        if not match:
            return None
        start, end, txt = match.groups()
        start, end = float(start), float(end)
        if end <= start:
            return None
        result.append({"start": start, "end": end, "text": txt.strip()})
    return result


def apply_timed_lines(groups: list[list[dict]], edited_lines: list[dict]) -> list[dict]:
    """Düzenlenmiş satırları orijinal kelime öbekleriyle eşleştirip yeniden birleştirir.

    Bir satırın zaman aralığı orijinal bir öbekle birebir eşleşiyorsa (kullanıcı sadece metni
    değiştirmiş, zamanı değiştirmemiş demektir), o öbeğin kelimeleriyle metin diff'lenir — böylece
    değişmeyen kelimeler zamanlarını korur. Eşleşme yoksa (zaman elle değiştirilmiş ya da yeni bir
    satır eklenmiş) satırın metni belirtilen aralığa eşit paylaştırılır.
    """
    group_by_bounds = {(round(g[0]["start"], 2), round(g[-1]["end"], 2)): g for g in groups}

    result: list[dict] = []
    for line in edited_lines:
        key = (round(line["start"], 2), round(line["end"], 2))
        original_group = group_by_bounds.get(key)
        if original_group is not None:
            result.extend(apply_text_diff(original_group, line["text"]))
            continue

        tokens = line["text"].split()
        if not tokens:
            continue
        span = max(line["end"] - line["start"], 0.05 * len(tokens))
        step = span / len(tokens)
        for k, tok in enumerate(tokens):
            result.append(
                {
                    "word": tok,
                    "start": round(line["start"] + k * step, 3),
                    "end": round(line["start"] + (k + 1) * step, 3),
                }
            )

    return result


def replace_words_in_range(all_words: list[dict], start: float, end: float, new_words: list[dict]) -> list[dict]:
    """[start, end] aralığındaki kelimeleri tamamen `new_words` ile değiştirir (sıra korunur)."""
    kept = [w for w in all_words if w["end"] <= start or w["start"] >= end]
    kept.extend(new_words)
    kept.sort(key=lambda w: w["start"])
    return kept
