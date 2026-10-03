"""Kelime bazlı zaman damgalarından "pop caption" tarzı .ass altyazı dosyası üretir.

Akan/kayan bir karaoke geçişi yerine her kelime için ayrı bir görüntüleme olayı (Dialogue) üretilir:
bir öbekteki kelimeler ekranda aynı anda durur, ama o an söylenen kelime vurgu rengine döner ve
hafifçe büyür; söylenmesi bitince anında normal renk/boyuta geri döner (anlık açma/kapama).
"""

import os

ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{font_size},{base_color_full},{base_color_full},{outline_color},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_width},1,{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

ALIGNMENT_MAP = {"bottom_center": 2, "middle_center": 5, "top_center": 8}
SENTENCE_ENDERS = (".", "!", "?", "…")
SOFT_BREAKS = (",", ";", ":")


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    cs = int(round((s - int(s)) * 100))
    if cs >= 100:
        cs = 0
        s = int(s) + 1
    return f"{h:d}:{m:02d}:{int(s):02d}.{cs:02d}"


def _inline_color(ass_color: str) -> str:
    """&HAABBGGRR (Style formatı) -> &HBBGGRR& (satır içi override tag formatı)."""
    core = ass_color.replace("&H", "").replace("&h", "").replace("&", "")
    if len(core) == 8:
        core = core[2:]
    return f"&H{core.zfill(6)}&"


def group_phrases(
    words: list[dict],
    max_words: int = 3,
    max_gap: float = 0.5,
) -> list[list[dict]]:
    """Kelimeleri en fazla `max_words` kelimelik doğal öbeklere böler.

    Bölme noktaları rastgele kelime sayısına göre değil, cümle sonu noktalaması, yumuşak
    duraklama noktalaması (virgül vb.) veya iki kelime arasındaki sessizlik (nefes molası)
    baz alınarak belirlenir.
    """
    groups: list[list[dict]] = []
    current: list[dict] = []

    for w in words:
        if current:
            prev = current[-1]
            gap = w["start"] - prev["end"]
            prev_text = prev["word"].strip()
            ends_sentence = prev_text.endswith(SENTENCE_ENDERS)
            ends_soft = prev_text.endswith(SOFT_BREAKS)
            if (
                len(current) >= max_words
                or gap > max_gap
                or ends_sentence
                or (ends_soft and len(current) >= 2)
            ):
                groups.append(current)
                current = []
        current.append(w)

    if current:
        groups.append(current)

    return groups


def build_ass(
    words: list[dict],
    output_path: str,
    style: dict,
    res_x: int = 1080,
    res_y: int = 1920,
) -> str:
    """words: [{"word","start","end"}] (segment/klip başlangıcına göre sıfırlanmış zamanlar)."""
    if not words:
        raise RuntimeError("Altyazı üretimi için kelime verisi bulunamadı.")

    alignment = ALIGNMENT_MAP.get(style.get("position", "bottom_center"), 2)
    margin_v_percent = style.get("margin_v_percent", 12)
    margin_v = int(res_y * margin_v_percent / 100)

    base_color = style.get("base_color", "&H00FFFFFF")
    highlight_color = style.get("highlight_color", "&H0000D7FF")
    base_inline = _inline_color(base_color)
    highlight_inline = _inline_color(highlight_color)
    grow_percent = style.get("word_grow_percent", 15)
    grow_scale = 100 + grow_percent
    max_words = style.get("words_per_line", 3)
    phrase_gap = style.get("phrase_gap_seconds", 0.5)

    header = ASS_HEADER_TEMPLATE.format(
        res_x=res_x,
        res_y=res_y,
        font=style.get("font", "Arial Black"),
        font_size=style.get("font_size", 84),
        base_color_full=base_color,
        outline_color=style.get("outline_color", "&H00000000"),
        outline_width=style.get("outline_width", 4),
        alignment=alignment,
        margin_v=margin_v,
    )

    lines = [header]
    for group in group_phrases(words, max_words=max_words, max_gap=phrase_gap):
        clean_words = [
            {**w, "word": w["word"].strip().replace("{", "").replace("}", "")} for w in group
        ]
        clean_words = [w for w in clean_words if w["word"]]
        if not clean_words:
            continue

        group_end = clean_words[-1]["end"]

        for i, active in enumerate(clean_words):
            event_start = active["start"]
            event_end = clean_words[i + 1]["start"] if i + 1 < len(clean_words) else group_end
            if event_end <= event_start:
                continue

            spans = []
            for j, w in enumerate(clean_words):
                if j == i:
                    spans.append(
                        f"{{\\c{highlight_inline}\\fscx{grow_scale}\\fscy{grow_scale}}}{w['word']}"
                    )
                else:
                    spans.append(f"{{\\c{base_inline}\\fscx100\\fscy100}}{w['word']}")
            text = " ".join(spans)

            lines.append(
                f"Dialogue: 0,{_fmt_time(event_start)},{_fmt_time(event_end)},Caption,,0,0,0,,{text}"
            )

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return output_path
