#!/usr/bin/env python3
"""YouTube Shorts Bot — Streamlit arayüzü.

Çalıştırma: streamlit run app.py
(ya da proje kökündeki start_app.command dosyasına çift tıklayın)
"""

import json
import os
import queue
import sys
import tempfile
import threading
import time

import streamlit as st
import yaml

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

import main as pipeline  # noqa: E402
from src import burner as burner_mod  # noqa: E402
from src import clipper as clipper_mod  # noqa: E402
from src import subtitles as subtitles_mod  # noqa: E402
from src import transcriber  # noqa: E402
from src.gui_utils import (  # noqa: E402
    ass_to_hex,
    hex_to_ass,
    load_yaml_config,
    read_env_value,
    save_yaml_config,
    write_env_value,
)

ENV_PATH = os.path.join(PROJECT_DIR, ".env")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.yaml")
OUTPUT_ROOT = os.path.join(PROJECT_DIR, "output")

st.set_page_config(page_title="YouTube Shorts Bot", page_icon="🎬", layout="wide")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
for key, default in {
    "queue": [],
    "batch_stage": "idle",  # idle -> analyzing -> review -> cutting -> done
    "batch_log_lines": [],
    "batch_log_queue": None,
    "batch_thread": None,
    "batch_holder": None,
    "batch_analyses": None,
    "batch_results": None,
    "selected_clips_per_video": None,
    "batch_tmp_config": None,
    "seo_result": None,
    "channel_scan_stage": "idle",  # idle -> running -> done
    "channel_scan_log_queue": None,
    "channel_scan_thread": None,
    "channel_scan_holder": None,
    "channel_scan_results": None,
    "channel_scan_top_error": None,
    "analytics_top_videos": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def make_gui_logger(q: "queue.Queue"):
    def gui_log(stage: str, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        q.put(f"[{timestamp}] [{stage}] {message}")

    return gui_log


def run_analyze_batch_thread(queue_items: list[dict], gui_config_path: str, holder: dict) -> None:
    results = []
    for idx, item in enumerate(queue_items, start=1):
        pipeline.log("batch", f"=== [{idx}/{len(queue_items)}] analiz ediliyor: {item['value']} ===")
        try:
            if item["source_type"] == "url":
                r = pipeline.analyze_source(item["value"], gui_config_path, manual_clips_arg=item.get("manual_clips"))
            else:
                r = pipeline.analyze_source(
                    None, gui_config_path, manual_clips_arg=item.get("manual_clips"), local_file=item["value"]
                )
            results.append(r)
        except Exception as e:  # noqa: BLE001
            pipeline.log("batch", f"HATA: {e}")
            results.append(None)
    holder["results"] = results
    holder["done"] = True


def run_cut_batch_thread(analyses: list[dict | None], selected_per_video: list[list], holder: dict) -> None:
    results = []
    for idx, (analysis, selected) in enumerate(zip(analyses, selected_per_video), start=1):
        if analysis is None:
            results.append(None)
            continue
        pipeline.log("batch", f"=== [{idx}/{len(analyses)}] kesiliyor: {analysis['video_id']} ===")
        try:
            r = pipeline.produce_clips(analysis, selected)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            pipeline.log("batch", f"HATA: {e}")
            results.append(None)
    holder["results"] = results
    holder["done"] = True


def build_gui_config_path() -> str:
    """GUI'den tetiklenen çalıştırmalarda yükleme her zaman kapalı tutulur: yükleme, sonuç
    galerisindeki 'YouTube'a Yükle' butonuyla ayrı ve açık onaylı olarak yapılır."""
    config = load_yaml_config(CONFIG_PATH)
    config.setdefault("upload", {})["enabled"] = False
    fd, tmp_path = tempfile.mkstemp(suffix=".yaml", prefix="gui_config_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Ortak: sonuç galerisi (üretim sonrası ve geçmiş sekmesinde kullanılır)
# ---------------------------------------------------------------------------
def build_clip_groups(clip_words_rel: list[dict], style: dict) -> list[list[dict]]:
    return subtitles_mod.group_phrases(
        clip_words_rel,
        max_words=style.get("words_per_line", 3),
        max_gap=style.get("phrase_gap_seconds", 0.5),
    )


def effective_subtitle_style(config: dict, clip: dict) -> dict:
    """Global altyazı stiline, bu klip için kaydedilmiş kişisel konum ayarı (varsa) uygulanmış
    hali — böylece klip bazlı konum ayarı, altyazı düzenleme/kırpma gibi diğer işlemlerde de
    korunur."""
    style = dict(config.get("subtitle_style", {}))
    override = clip.get("subtitle_position")
    if override:
        style.update(override)
    return style


def apply_transcript_text_edit(
    video_id: str, clip: dict, groups: list[list[dict]], original_text: str, edited_text: str, config: dict
) -> None:
    if edited_text.strip() == original_text.strip():
        st.info("Değişiklik yok.")
        return

    edited_lines = transcriber.parse_timed_lines(edited_text)
    if edited_lines is None:
        st.error(
            "Format hatalı. Her satır `BAŞLANGIÇ-BİTİŞ: metin` şeklinde olmalı, "
            'örn. `0.00-1.03: Merhaba ben enes`. Zaman aralıklarını (satır başlarını) silmeyin.'
        )
        return

    transcript_path = os.path.join(PROJECT_DIR, "downloads", f"{video_id}_transcript.json")
    if not os.path.exists(transcript_path):
        st.error(f"Transkript bulunamadı: {transcript_path}")
        return

    transcript = transcriber.load_transcript(transcript_path)
    new_words_rel = transcriber.apply_timed_lines(groups, edited_lines)
    new_words_abs = [
        {"word": w["word"], "start": round(w["start"] + clip["start"], 3), "end": round(w["end"] + clip["start"], 3)}
        for w in new_words_rel
    ]

    transcript["words"] = transcriber.replace_words_in_range(
        transcript["words"], clip["start"], clip["end"], new_words_abs
    )
    transcriber.save_transcript(transcript, transcript_path)

    res = config.get("output_resolution", {"width": 1080, "height": 1920})
    clip_words = pipeline.words_in_range(transcript["words"], clip["start"], clip["end"])
    subtitles_mod.build_ass(
        clip_words, clip["ass_path"], effective_subtitle_style(config, clip),
        res_x=res["width"], res_y=res["height"],
    )
    burner_mod.burn_subtitles(
        clip["raw_path"], clip["ass_path"], clip["final_path"],
        watermark_cfg=config.get("watermark"), fade_cfg=clip.get("fade"), duration=clip["end"] - clip["start"],
    )
    st.success(f"{clip['prefix']} altyazısı güncellendi.")
    st.rerun()


def apply_trim(video_id: str, output_dir: str, clip: dict, rel_start: float, rel_end: float, config: dict) -> None:
    if rel_end - rel_start < 3:
        st.error("Klip en az 3 saniye olmalı.")
        return

    new_abs_start = round(clip["start"] + rel_start, 3)
    new_abs_end = round(clip["start"] + rel_end, 3)

    trimmed_raw = clip["raw_path"] + ".trimmed.mp4"
    try:
        clipper_mod.trim_video(clip["raw_path"], rel_start, rel_end, trimmed_raw)
    except Exception as e:  # noqa: BLE001
        st.error(f"Kırpma başarısız: {e}")
        if os.path.exists(trimmed_raw):
            os.remove(trimmed_raw)
        return
    os.replace(trimmed_raw, clip["raw_path"])

    pipeline.update_clip_bounds(output_dir, clip["prefix"], new_abs_start, new_abs_end)

    transcript_path = os.path.join(PROJECT_DIR, "downloads", f"{video_id}_transcript.json")
    transcript = transcriber.load_transcript(transcript_path)
    res = config.get("output_resolution", {"width": 1080, "height": 1920})
    clip_words = pipeline.words_in_range(transcript["words"], new_abs_start, new_abs_end)
    subtitles_mod.build_ass(
        clip_words, clip["ass_path"], effective_subtitle_style(config, clip),
        res_x=res["width"], res_y=res["height"],
    )
    burner_mod.burn_subtitles(
        clip["raw_path"], clip["ass_path"], clip["final_path"],
        watermark_cfg=config.get("watermark"), fade_cfg=clip.get("fade"), duration=new_abs_end - new_abs_start,
    )

    st.success(f"{clip['prefix']} kırpıldı: yeni süre {new_abs_end - new_abs_start:.1f}s")
    st.rerun()


def do_upload(clip: dict, title: str, description: str, tags: list, thumb_path: str | None, upload_cfg: dict) -> None:
    from src.uploader import compute_publish_time, get_authenticated_service, upload_video

    try:
        with st.spinner("YouTube'a yükleniyor... (ilk seferde tarayıcıda izin isteyebilir)"):
            youtube = get_authenticated_service(
                upload_cfg.get("credentials_path", "credentials.json"),
                upload_cfg.get("token_path", "token.json"),
            )
            schedule_cfg = upload_cfg.get("schedule", {})
            publish_at = compute_publish_time(
                1, schedule_cfg.get("start_delay_hours", 1), schedule_cfg.get("interval_hours", 6)
            )
            yt_video_id = upload_video(
                youtube, clip["final_path"], title, description, tags,
                category_id=upload_cfg.get("category_id", "20"),
                privacy_status=upload_cfg.get("privacy_status", "private"),
                publish_at=publish_at,
                thumbnail_path=thumb_path,
            )
        st.success(f"Yüklendi: https://youtu.be/{yt_video_id}")
    except Exception as e:  # noqa: BLE001
        st.error(f"Yükleme başarısız: {e}")


def apply_reposition(video_id: str, output_dir: str, clip: dict, x_offset: float, scale: float, config: dict) -> None:
    source_video_path = clip.get("source_video_path")
    if not source_video_path or not os.path.exists(source_video_path):
        st.error("Bu klip eski bir sürümle üretildiği için yeniden konumlandırılamıyor (kaynak video bulunamadı).")
        return

    res = config.get("output_resolution", {"width": 1080, "height": 1920})
    tmp_raw = clip["raw_path"] + ".repos.mp4"
    try:
        clipper_mod.cut_and_verticalize(
            source_video_path, clip["start"], clip["end"], tmp_raw,
            width=res["width"], height=res["height"],
            mode=config.get("vertical_mode", "blur_pad"),
            zoom_config=config.get("intro_zoom"),
            x_offset=x_offset,
            scale=scale,
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Yeniden konumlandırma başarısız: {e}")
        if os.path.exists(tmp_raw):
            os.remove(tmp_raw)
        return
    os.replace(tmp_raw, clip["raw_path"])

    burner_mod.burn_subtitles(
        clip["raw_path"], clip["ass_path"], clip["final_path"],
        watermark_cfg=config.get("watermark"), fade_cfg=clip.get("fade"), duration=clip["end"] - clip["start"],
    )

    meta_path = os.path.join(output_dir, f"metadata_{clip['prefix']}.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["x_offset"] = x_offset
    meta["scale"] = scale
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    st.success(f"{clip['prefix']} yeniden konumlandırıldı.")


def apply_fade(
    output_dir: str, clip: dict, enabled: bool, fade_in_seconds: float, fade_out_seconds: float, config: dict
) -> None:
    fade_cfg = {"enabled": enabled, "fade_in_seconds": fade_in_seconds, "fade_out_seconds": fade_out_seconds}
    duration = clip["end"] - clip["start"]

    try:
        burner_mod.burn_subtitles(
            clip["raw_path"], clip["ass_path"], clip["final_path"],
            watermark_cfg=config.get("watermark"), fade_cfg=fade_cfg, duration=duration,
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Fade uygulanamadı: {e}")
        return

    meta_path = os.path.join(output_dir, f"metadata_{clip['prefix']}.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["fade"] = fade_cfg
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    st.success(f"{clip['prefix']} için giriş/çıkış geçişi güncellendi.")
    st.rerun()


def apply_subtitle_position(
    video_id: str, output_dir: str, clip: dict, position: str, margin_v_percent: float, config: dict
) -> None:
    transcript_path = os.path.join(PROJECT_DIR, "downloads", f"{video_id}_transcript.json")
    if not os.path.exists(transcript_path):
        st.error(f"Transkript bulunamadı: {transcript_path}")
        return

    subtitle_position = {"position": position, "margin_v_percent": margin_v_percent}
    style = dict(config.get("subtitle_style", {}))
    style.update(subtitle_position)

    transcript = transcriber.load_transcript(transcript_path)
    res = config.get("output_resolution", {"width": 1080, "height": 1920})
    clip_words = pipeline.words_in_range(transcript["words"], clip["start"], clip["end"])
    subtitles_mod.build_ass(clip_words, clip["ass_path"], style, res_x=res["width"], res_y=res["height"])

    try:
        burner_mod.burn_subtitles(
            clip["raw_path"], clip["ass_path"], clip["final_path"],
            watermark_cfg=config.get("watermark"), fade_cfg=clip.get("fade"), duration=clip["end"] - clip["start"],
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Altyazı konumu uygulanamadı: {e}")
        return

    meta_path = os.path.join(output_dir, f"metadata_{clip['prefix']}.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["subtitle_position"] = subtitle_position
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    st.success(f"{clip['prefix']} için altyazı konumu güncellendi.")
    st.rerun()


def render_output_gallery(video_id: str, output_dir: str, key_prefix: str = "gallery") -> None:
    config = load_yaml_config(CONFIG_PATH)
    resumable = pipeline.find_resumable_clips(output_dir)
    if not resumable:
        st.warning("Bu videoya ait gösterilebilir short bulunamadı.")
        return

    agg_path = os.path.join(output_dir, "upload_metadata.json")
    agg_by_path = {}
    if os.path.exists(agg_path):
        with open(agg_path, "r", encoding="utf-8") as f:
            for e in json.load(f):
                agg_by_path[e["clip_dosyasi"]] = e

    transcript_path = os.path.join(PROJECT_DIR, "downloads", f"{video_id}_transcript.json")
    transcript_words = transcriber.load_transcript(transcript_path)["words"] if os.path.exists(transcript_path) else []

    upload_cfg = config.get("upload", {})
    cols = st.columns(2)

    for i, clip in enumerate(resumable):
        meta_path = os.path.join(output_dir, f"metadata_{clip['prefix']}.json")
        meta = {}
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        agg = agg_by_path.get(clip["final_path"], {})

        title = agg.get("baslik") or meta.get("title", clip["prefix"])
        description = agg.get("aciklama") or meta.get("reason", "")
        tags = agg.get("etiketler", [])
        source = meta.get("source", "?")
        score = meta.get("score")
        thumb_path = os.path.join(output_dir, f"{clip['prefix']}_thumbnail.png")

        with cols[i % 2]:
            with st.container(border=True):
                badge = "🤖 AI" if source == "ai" else "✋ Manuel"
                score_str = f" · skor {score}" if score is not None else ""
                st.markdown(f"**{clip['prefix']}** — {badge}{score_str}")

                if os.path.exists(clip["final_path"]):
                    st.video(clip["final_path"])

                st.markdown(f"**{title}**")
                if description:
                    st.caption(description)
                if tags:
                    st.caption("Etiketler: " + ", ".join(tags))
                if os.path.exists(thumb_path):
                    st.image(thumb_path, caption="Thumbnail", width=220)

                with st.expander("✏️ Altyazı metnini düzenle"):
                    st.caption(
                        "Her satır ekranda aynı anda çıkan bir altyazı öbeğidir, başındaki zaman "
                        "aralığı o öbeğin videodaki saniyesidir. Zaman kısmına dokunmadan sadece "
                        "metni düzeltip butona basın."
                    )
                    clip_words_rel = pipeline.words_in_range(transcript_words, clip["start"], clip["end"])
                    groups = build_clip_groups(clip_words_rel, config.get("subtitle_style", {}))
                    original_text = transcriber.words_to_timed_lines(groups)
                    edited_text = st.text_area(
                        "Altyazı metni (zaman aralığı: metin)",
                        value=original_text,
                        key=f"{key_prefix}_txt_{video_id}_{clip['prefix']}",
                        height=140,
                    )
                    if st.button("Düzenle ve Yeniden Yak", key=f"{key_prefix}_editbtn_{video_id}_{clip['prefix']}"):
                        apply_transcript_text_edit(video_id, clip, groups, original_text, edited_text, config)

                clip_duration = clip["end"] - clip["start"]
                with st.expander(f"✂️ Süreyi kırp (şu an {clip_duration:.1f}s)"):
                    st.caption("İki uçtan da kısabilirsiniz — örn. sadece ilk 30 saniyeyi tutmak için sağ ucu 30'a çekin.")
                    trim_range = st.slider(
                        "Kullanılacak aralık (saniye)",
                        min_value=0.0,
                        max_value=float(clip_duration),
                        value=(0.0, float(clip_duration)),
                        step=0.5,
                        key=f"{key_prefix}_trim_{video_id}_{clip['prefix']}",
                    )
                    st.caption(f"Yeni süre: {trim_range[1] - trim_range[0]:.1f}s")
                    if st.button("✂️ Kırp", key=f"{key_prefix}_trimbtn_{video_id}_{clip['prefix']}"):
                        apply_trim(video_id, output_dir, clip, trim_range[0], trim_range[1], config)

                with st.expander("🎯 Görüntü konumu ve ölçeği"):
                    if clip.get("source_video_path") and os.path.exists(clip["source_video_path"]):
                        st.caption(
                            "Ana konu kadrajın ortasında değilse, kadrajı kaynak görüntü içinde yatay "
                            "olarak kaydırın (-1 tam sol, 0 orta, 1 tam sağ). Ölçekle görüntüyü "
                            "büyütüp (yakınlaştırır) küçültebilirsiniz (1.0 = normal boyut)."
                        )
                        offset_val = st.slider(
                            "Yatay konum",
                            min_value=-1.0,
                            max_value=1.0,
                            value=float(clip.get("x_offset", 0.0)),
                            step=0.05,
                            key=f"{key_prefix}_offset_{video_id}_{clip['prefix']}",
                        )
                        scale_val = st.slider(
                            "Ölçek (büyüt/küçült)",
                            min_value=0.5,
                            max_value=2.0,
                            value=float(clip.get("scale", 1.0)),
                            step=0.05,
                            key=f"{key_prefix}_scale_{video_id}_{clip['prefix']}",
                        )
                        if st.button("🎯 Uygula", key=f"{key_prefix}_offsetbtn_{video_id}_{clip['prefix']}"):
                            apply_reposition(video_id, output_dir, clip, offset_val, scale_val, config)
                    else:
                        st.caption("Bu klip eski bir sürümle üretildiği için yeniden konumlandırılamıyor.")

                with st.expander("📍 Altyazı konumu"):
                    st.caption(
                        "Altyazılar YouTube Shorts arayüzünde (açıklama, butonlar) çok aşağıda kalıp "
                        "görünmüyorsa buradan yukarı taşıyabilirsiniz."
                    )
                    current_sub_style = effective_subtitle_style(config, clip)
                    position_opts = ["bottom_center", "middle_center", "top_center"]
                    position_labels = {"bottom_center": "Alt", "middle_center": "Orta", "top_center": "Üst"}
                    current_position = current_sub_style.get("position", "bottom_center")
                    sub_position = st.selectbox(
                        "Dikey konum",
                        position_opts,
                        index=position_opts.index(current_position) if current_position in position_opts else 0,
                        format_func=lambda p: position_labels[p],
                        key=f"{key_prefix}_subpos_{video_id}_{clip['prefix']}",
                    )
                    sub_margin = st.slider(
                        "Kenardan uzaklık (%)",
                        min_value=0, max_value=40,
                        value=int(current_sub_style.get("margin_v_percent", 12)),
                        key=f"{key_prefix}_submargin_{video_id}_{clip['prefix']}",
                    )
                    if st.button("📍 Uygula", key=f"{key_prefix}_subposbtn_{video_id}_{clip['prefix']}"):
                        apply_subtitle_position(video_id, output_dir, clip, sub_position, sub_margin, config)

                clip_fade = clip.get("fade", {})
                with st.expander("🌗 Karartma / Aydınlatma (Fade in-out)"):
                    st.caption(
                        "Klip karanlıktan açılıp (giriş) sonunda karanlığa kapanabilir (çıkış). "
                        "Süreleri saniye cinsinden ayarlayıp Uygula'ya basın; istemezseniz kapalı bırakın."
                    )
                    fade_enabled = st.checkbox(
                        "Aktif et", value=bool(clip_fade.get("enabled", False)),
                        key=f"{key_prefix}_fadeon_{video_id}_{clip['prefix']}",
                    )
                    fade_c1, fade_c2 = st.columns(2)
                    with fade_c1:
                        fade_in_val = st.number_input(
                            "Giriş süresi (sn)", min_value=0.0, max_value=float(clip["end"] - clip["start"]),
                            value=float(clip_fade.get("fade_in_seconds", 1.0)), step=0.1,
                            key=f"{key_prefix}_fadein_{video_id}_{clip['prefix']}",
                        )
                    with fade_c2:
                        fade_out_val = st.number_input(
                            "Çıkış süresi (sn)", min_value=0.0, max_value=float(clip["end"] - clip["start"]),
                            value=float(clip_fade.get("fade_out_seconds", 1.0)), step=0.1,
                            key=f"{key_prefix}_fadeout_{video_id}_{clip['prefix']}",
                        )
                    if st.button("🌗 Uygula", key=f"{key_prefix}_fadebtn_{video_id}_{clip['prefix']}"):
                        apply_fade(output_dir, clip, fade_enabled, fade_in_val, fade_out_val, config)

                if upload_cfg.get("enabled"):
                    if st.button("📤 YouTube'a Yükle", key=f"{key_prefix}_upload_{video_id}_{clip['prefix']}"):
                        thumb_arg = thumb_path if os.path.exists(thumb_path) else None
                        do_upload(clip, title, description, tags, thumb_arg, upload_cfg)


def render_seo_result(result: dict, key_prefix: str) -> None:
    """Bir video için üretilmiş SEO önerisini (eski/yeni başlık, açıklama, etiketler)
    kopyalanabilir kutularda gösterir. Tek-video aracı ve kanal geneli tarama tarafından
    ortak kullanılır."""
    if result.get("error"):
        st.error(f"Bu video işlenemedi: {result['error']}")
        return
    col_old, col_new = st.columns(2)
    with col_old:
        st.markdown("**Eski başlık**")
        st.text_input(
            "Eski başlık", value=result.get("old_title", ""), disabled=True,
            label_visibility="collapsed", key=f"{key_prefix}_old_title",
        )
    with col_new:
        st.markdown("**Önerilen yeni başlık**")
        st.text_input(
            "Yeni başlık", value=result["title"], label_visibility="collapsed",
            key=f"{key_prefix}_new_title",
        )
    st.markdown("**Önerilen açıklama**")
    st.text_area(
        "Yeni açıklama", value=result["description"], height=120,
        label_visibility="collapsed", key=f"{key_prefix}_new_desc",
    )
    st.markdown("**Önerilen etiketler**")
    st.text_area(
        "Yeni etiketler", value=", ".join(result["tags"]), height=80,
        label_visibility="collapsed", key=f"{key_prefix}_new_tags",
    )


def run_channel_scan_thread(
    gui_config_path: str, credentials_path: str, token_path: str, force: bool, limit: int | None,
    video_type: str, holder: dict,
) -> None:
    try:
        results = pipeline.run_channel_seo_scan(
            gui_config_path, credentials_path=credentials_path, token_path=token_path, force=force, limit=limit,
            video_type=video_type,
        )
        holder["results"] = results
    except Exception as e:  # noqa: BLE001
        pipeline.log("channel_seo", f"HATA: {e}")
        holder["results"] = []
        holder["error"] = str(e)
    holder["done"] = True


# ---------------------------------------------------------------------------
# Sayfa
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1200px; }
      div[data-testid="stTabs"] button[role="tab"] { font-size: 1rem; padding: 0.6rem 1.1rem; }
      .stButton>button { border-radius: 8px; font-weight: 600; }
      div[data-testid="stExpander"] { border-radius: 10px; }
      hr { margin: 1.2rem 0; opacity: 0.15; }
    </style>
    <div style="display:flex; align-items:center; gap:0.7rem; margin-bottom:0.3rem;">
      <span style="font-size:2.1rem;">🎬</span>
      <div>
        <div style="font-size:1.7rem; font-weight:700; line-height:1.15;">YouTube Shorts Bot</div>
        <div style="opacity:0.55; font-size:0.92rem;">İndirme → altyazı → dikey klip → SEO → yükleme, tek arayüzden</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.divider()

tab_generate, tab_history, tab_seo, tab_settings = st.tabs(
    ["🚀 Üretim", "📂 Geçmiş Çıktılar", "🔍 Eski Video SEO", "⚙️ Ayarlar"]
)

with tab_generate:
    stage = st.session_state.batch_stage

    # ---------------------------------------------------------------
    # 1) idle: kuyruk oluşturma
    # ---------------------------------------------------------------
    if stage == "idle":
        st.subheader("Kuyruk")
        if st.session_state.queue:
            for i, item in enumerate(st.session_state.queue):
                cols = st.columns([6, 1])
                label = item["value"] if item["source_type"] == "url" else f"📁 {item['value']}"
                extra = f"  ·  manuel: {item['manual_clips']}" if item.get("manual_clips") else ""
                cols[0].write(f"**{i + 1}.** {label}{extra}")
                if cols[1].button("Kaldır", key=f"rm_{i}"):
                    st.session_state.queue.pop(i)
                    st.rerun()
        else:
            st.caption("Kuyruk boş — aşağıdan video ekleyin. Birden fazla video ekleyip hepsini tek seferde analiz ettirebilirsiniz.")

        st.divider()
        st.markdown("**➕ Video Ekle**")
        source_mode = st.radio("Kaynak", ["YouTube linki", "Bilgisayardan dosya"], horizontal=True, key="add_src_mode")
        if source_mode == "YouTube linki":
            add_value = st.text_input("YouTube video linki", key="add_url", placeholder="https://www.youtube.com/watch?v=...")
            src_type = "url"
        else:
            add_value = st.text_input("Video dosyasının tam yolu", key="add_local", placeholder="/Users/kullanici/Videolar/video.mp4")
            src_type = "local"
        add_manual = st.text_input(
            "Manuel klip aralığı (opsiyonel)",
            key="add_manual",
            placeholder="00:12-00:45,03:20-04:10",
            help="AI seçimine ek olarak bu videodan kesilecek zaman aralıkları (virgülle ayırın).",
        )
        if st.button("➕ Kuyruğa Ekle"):
            if add_value:
                st.session_state.queue.append(
                    {"source_type": src_type, "value": add_value, "manual_clips": add_manual or None}
                )
                st.rerun()
            else:
                st.error("Bir link ya da dosya yolu girin.")

        if st.session_state.queue:
            st.divider()
            if st.button(f"🔍 Kuyruğu Analiz Et ({len(st.session_state.queue)} video)", type="primary"):
                q = queue.Queue()
                st.session_state.batch_log_queue = q
                st.session_state.batch_log_lines = []
                holder = {"done": False, "results": None}
                st.session_state.batch_holder = holder
                pipeline.log = make_gui_logger(q)
                gui_config_path = build_gui_config_path()
                st.session_state.batch_tmp_config = gui_config_path

                t = threading.Thread(
                    target=run_analyze_batch_thread,
                    args=(list(st.session_state.queue), gui_config_path, holder),
                    daemon=True,
                )
                st.session_state.batch_thread = t
                t.start()
                st.session_state.batch_stage = "analyzing"
                st.rerun()

    # ---------------------------------------------------------------
    # 2) analyzing: canlı log (indirme + transkript + Gemini, video video sırayla)
    # ---------------------------------------------------------------
    elif stage == "analyzing":
        st.info("⏳ Videolar analiz ediliyor (indirme + transkript + Gemini)... Bu uzun sürebilir.")
        q = st.session_state.batch_log_queue
        while q is not None and not q.empty():
            st.session_state.batch_log_lines.append(q.get())
        st.code("\n".join(st.session_state.batch_log_lines[-300:]) or "Başlatılıyor...")

        holder = st.session_state.batch_holder
        if holder["done"]:
            st.session_state.batch_thread = None
            st.session_state.batch_analyses = holder["results"]
            st.session_state.batch_stage = "review"
            st.rerun()
        else:
            time.sleep(1)
            st.rerun()

    # ---------------------------------------------------------------
    # 3) review: hangi adayların kesileceğini seçme
    # ---------------------------------------------------------------
    elif stage == "review":
        st.subheader("Aday İnceleme — Kesmek istediklerinizi seçin")
        st.caption("Varsayılan olarak her videonun en iyi skorlu adayları (config'teki num_clips kadar) işaretlidir.")
        analyses = st.session_state.batch_analyses
        any_valid = False

        for vi, analysis in enumerate(analyses):
            if analysis is None:
                st.error(f"Video {vi + 1}: analiz başarısız oldu, atlanıyor (loglara bakın).")
                continue
            any_valid = True
            with st.container(border=True):
                st.markdown(f"### {analysis['video_id']}")
                num_clips = analysis["config"]["num_clips"]
                for ci, cand in enumerate(analysis["candidates"]):
                    default_checked = ci < num_clips
                    label = f"skor {cand['score']} · [{cand['start']:.1f}s-{cand['end']:.1f}s] · **{cand['title']}**"
                    st.checkbox(label, value=default_checked, key=f"sel_{vi}_ai_{ci}")
                    st.caption(cand["reason"])
                for mclip in analysis["manual_clips"]:
                    st.write(f"✋ Manuel klip [{mclip['start']:.1f}s-{mclip['end']:.1f}s] (her zaman dahil edilir)")

        col1, col2 = st.columns(2)
        if any_valid and col1.button("✂️ Seçilenleri Kes", type="primary"):
            selected_per_video = []
            for vi, analysis in enumerate(analyses):
                if analysis is None:
                    selected_per_video.append([])
                    continue
                sel = [
                    cand
                    for ci, cand in enumerate(analysis["candidates"])
                    if st.session_state.get(f"sel_{vi}_ai_{ci}", False)
                ]
                sel.extend(analysis["manual_clips"])
                selected_per_video.append(sel)
            st.session_state.selected_clips_per_video = selected_per_video

            q = queue.Queue()
            st.session_state.batch_log_queue = q
            st.session_state.batch_log_lines = []
            holder = {"done": False, "results": None}
            st.session_state.batch_holder = holder
            pipeline.log = make_gui_logger(q)

            t = threading.Thread(
                target=run_cut_batch_thread,
                args=(analyses, selected_per_video, holder),
                daemon=True,
            )
            st.session_state.batch_thread = t
            t.start()
            st.session_state.batch_stage = "cutting"
            st.rerun()

        if col2.button("⬅️ Kuyruğa dön (analizi at)"):
            st.session_state.batch_stage = "idle"
            st.session_state.batch_analyses = None
            st.rerun()

    # ---------------------------------------------------------------
    # 4) cutting: canlı log (kırpma + altyazı + yakma, video video sırayla)
    # ---------------------------------------------------------------
    elif stage == "cutting":
        st.info("✂️ Seçilen klipler kesiliyor...")
        q = st.session_state.batch_log_queue
        while q is not None and not q.empty():
            st.session_state.batch_log_lines.append(q.get())
        st.code("\n".join(st.session_state.batch_log_lines[-300:]) or "Başlatılıyor...")

        holder = st.session_state.batch_holder
        if holder["done"]:
            st.session_state.batch_thread = None
            st.session_state.batch_results = holder["results"]
            if st.session_state.batch_tmp_config and os.path.exists(st.session_state.batch_tmp_config):
                os.remove(st.session_state.batch_tmp_config)
                st.session_state.batch_tmp_config = None
            st.session_state.batch_stage = "done"
            st.rerun()
        else:
            time.sleep(1)
            st.rerun()

    # ---------------------------------------------------------------
    # 5) done: sonuç galerileri
    # ---------------------------------------------------------------
    elif stage == "done":
        st.success("✅ Tamamlandı!")
        for result in st.session_state.batch_results:
            if result is None:
                continue
            st.divider()
            st.subheader(result["video_id"])
            render_output_gallery(result["video_id"], result["output_dir"], key_prefix="gen")

        st.divider()
        if st.button("🔄 Yeni Kuyruk Başlat"):
            st.session_state.queue = []
            st.session_state.batch_analyses = None
            st.session_state.batch_results = None
            st.session_state.selected_clips_per_video = None
            st.session_state.batch_stage = "idle"
            st.rerun()

with tab_history:
    st.subheader("Geçmiş Üretimler")
    video_ids = (
        sorted(d for d in os.listdir(OUTPUT_ROOT) if os.path.isdir(os.path.join(OUTPUT_ROOT, d)))
        if os.path.isdir(OUTPUT_ROOT)
        else []
    )
    if not video_ids:
        st.info("Henüz üretilmiş bir çıktı yok.")
    else:
        chosen = st.selectbox("Video seçin", video_ids)
        if chosen:
            render_output_gallery(chosen, os.path.join(OUTPUT_ROOT, chosen), key_prefix="hist")

with tab_seo:
    st.subheader("Eski Video İçin Başlık/Açıklama/Etiket Önerisi")
    st.caption(
        "Kanalınızdaki eski, başlık/açıklama/etiketleri zayıf kalmış bir video için yeni öneriler "
        "üretir. Hiçbir şeyi otomatik değiştirmez — sadece öneri gösterir, YouTube Studio'ya elle "
        "kopyalamanız gerekir."
    )

    source_mode = st.radio("Girdi türü", ["Video linki", "Transkripti elle yapıştır"], horizontal=True, key="seo_source_mode")

    seo_url = ""
    seo_transcript = ""
    if source_mode == "Video linki":
        seo_url = st.text_input("YouTube video linki", key="seo_url")
        st.caption(
            "YouTube'un kendi altyazısını kullanır (genelde birkaç saniye sürer, bilgisayarınıza "
            "hiçbir şey indirilmez). Altyazı yoksa hata verir — o durumda transkripti elle "
            "yapıştırmanız gerekir."
        )
    else:
        seo_transcript = st.text_area("Transkript metni", height=200, key="seo_transcript")

    seo_current_title = st.text_input("Mevcut başlık (opsiyonel, bağlam için önerilir)", key="seo_current_title")

    if st.button("🔍 Öneri Üret", key="seo_generate_btn", type="primary"):
        if source_mode == "Video linki" and not seo_url.strip():
            st.error("Bir video linki girin.")
        elif source_mode == "Transkripti elle yapıştır" and not seo_transcript.strip():
            st.error("Transkript metni girin.")
        else:
            gui_config_path = build_gui_config_path()
            log_q: queue.Queue = queue.Queue()
            pipeline.log = make_gui_logger(log_q)
            try:
                with st.spinner("Öneri üretiliyor..."):
                    result = pipeline.generate_seo_suggestion(
                        gui_config_path,
                        url=seo_url.strip() or None,
                        transcript_text=seo_transcript.strip() or None,
                        current_title=seo_current_title.strip(),
                        allow_audio_fallback=False,
                    )
                st.session_state.seo_result = result
            except Exception as e:  # noqa: BLE001
                st.error(f"Öneri üretilemedi: {e}")
            finally:
                log_lines = []
                while not log_q.empty():
                    log_lines.append(log_q.get())
                if log_lines:
                    with st.expander("İşlem günlüğü"):
                        st.code("\n".join(log_lines))

    if st.session_state.get("seo_result"):
        st.divider()
        render_seo_result(st.session_state.seo_result, key_prefix="seo_single")
        st.caption("Bu alanları seçip kopyalayarak YouTube Studio'daki video düzenleme ekranına yapıştırabilirsiniz.")

    st.divider()
    st.subheader("Kanal Geneli Tarama")
    st.caption(
        "Kanalınızdaki videoları otomatik listeleyip her biri için tek tek öneri üretir. "
        "credentials.json kurulumu gerekir (SETUP.md) — sadece kimlik doğrulama için, kanala "
        "hiçbir şey yazılmaz/yüklenmez. Sadece herkese açık videolar dahil edilir (gizli/liste "
        "dışı/silinmiş videolar otomatik atlanır). Öncelikle YouTube'un kendi altyazısı "
        "kullanılır (hızlı, indirme yok); sadece altyazısı olmayan videolarda ses indirip "
        "Whisper'a düşülür — sonuçlar önbelleklenir, kesilirse kaldığı yerden devam edebilirsiniz."
    )

    cs_col1, cs_col2, cs_col3 = st.columns(3)
    with cs_col1:
        cs_force = st.checkbox("Daha önce işlenmiş videoları da yeniden işle", value=False, key="cs_force")
    with cs_col2:
        cs_limit = st.number_input("En fazla kaç video (0 = tümü)", min_value=0, value=0, key="cs_limit")
    with cs_col3:
        cs_video_type_options = ["Sadece Yatay (Uzun)", "Sadece Shorts", "Tümü"]
        cs_video_type_label = st.selectbox("Video türü", cs_video_type_options, key="cs_video_type")
        cs_video_type = {"Sadece Yatay (Uzun)": "long", "Sadece Shorts": "shorts", "Tümü": "all"}[cs_video_type_label]

    if st.session_state.channel_scan_stage == "idle":
        if st.button("📡 Kanalımı Tara", key="cs_start_btn", type="primary"):
            gui_config_path = build_gui_config_path()
            log_q: queue.Queue = queue.Queue()
            pipeline.log = make_gui_logger(log_q)
            holder: dict = {}
            thread = threading.Thread(
                target=run_channel_scan_thread,
                args=(
                    gui_config_path, "credentials.json", "token.json", cs_force, cs_limit or None,
                    cs_video_type, holder,
                ),
                daemon=True,
            )
            st.session_state.channel_scan_log_queue = log_q
            st.session_state.channel_scan_holder = holder
            st.session_state.channel_scan_thread = thread
            st.session_state.channel_scan_stage = "running"
            thread.start()
            st.rerun()

    elif st.session_state.channel_scan_stage == "running":
        st.info("Kanal taranıyor... (OAuth için ilk seferde bir tarayıcı penceresi açılabilir)")
        log_q = st.session_state.channel_scan_log_queue
        log_lines = []
        while not log_q.empty():
            log_lines.append(log_q.get())
        if log_lines:
            st.session_state.setdefault("cs_log_lines", [])
            st.session_state["cs_log_lines"] = st.session_state.get("cs_log_lines", []) + log_lines
        with st.expander("İşlem günlüğü", expanded=True):
            st.code("\n".join(st.session_state.get("cs_log_lines", [])[-200:]))

        holder = st.session_state.channel_scan_holder
        if holder.get("done"):
            st.session_state.channel_scan_results = holder.get("results", [])
            st.session_state.channel_scan_top_error = holder.get("error")
            st.session_state.channel_scan_stage = "done"
            st.session_state["cs_log_lines"] = []
            st.rerun()
        else:
            time.sleep(1)
            st.rerun()

    elif st.session_state.channel_scan_stage == "done":
        top_error = st.session_state.get("channel_scan_top_error")
        results = st.session_state.channel_scan_results or []
        if top_error:
            st.error(f"Tarama başlatılamadı: {top_error}")
        ok_results = [r for r in results if not r.get("error")]
        err_results = [r for r in results if r.get("error")]
        if results or not top_error:
            st.success(f"✅ Tarama tamamlandı: {len(ok_results)} video işlendi, {len(err_results)} hata.")
        for r in results:
            with st.expander(f"{r.get('old_title', r['video_id'])} ({r['video_id']})"):
                render_seo_result(r, key_prefix=f"cs_{r['video_id']}")
        if st.button("🔄 Yeni Tarama Başlat", key="cs_reset_btn"):
            st.session_state.channel_scan_stage = "idle"
            st.session_state.channel_scan_results = None
            st.session_state.channel_scan_holder = None
            st.session_state.channel_scan_thread = None
            st.rerun()

with tab_settings:
    st.subheader("Gemini API Anahtarı")
    existing_key = read_env_value(ENV_PATH, "GOOGLE_API_KEY")
    new_key = st.text_input("GOOGLE_API_KEY", value=existing_key, type="password")
    if st.button("API Anahtarını Kaydet"):
        write_env_value(ENV_PATH, "GOOGLE_API_KEY", new_key)
        st.success("Kaydedildi (.env güncellendi).")

    st.caption(
        "YouTube arama tabanlı başlık üretimi için ayrıca bir YOUTUBE_API_KEY gerekir "
        "(Google Cloud Console'da 'YouTube Data API v3'ü etkinleştirip bir API anahtarı oluşturun — "
        "OAuth kurulumuna gerek yok, salt-okunur arama için basit bir anahtar yeterli)."
    )
    existing_yt_key = read_env_value(ENV_PATH, "YOUTUBE_API_KEY")
    new_yt_key = st.text_input("YOUTUBE_API_KEY", value=existing_yt_key, type="password")
    if st.button("YouTube API Anahtarını Kaydet"):
        write_env_value(ENV_PATH, "YOUTUBE_API_KEY", new_yt_key)
        st.success("Kaydedildi (.env güncellendi).")

    st.divider()
    st.subheader("Genel Ayarlar")
    config = load_yaml_config(CONFIG_PATH)

    col1, col2 = st.columns(2)
    with col1:
        config["num_clips"] = st.number_input(
            "Kaç short üretilsin", min_value=0, max_value=10, value=config.get("num_clips", 3),
            help="0 yaparsanız inceleme ekranında hiçbir AI adayı otomatik işaretlenmez — "
                 "sadece manuel eklediğiniz klip(ler) seçili gelir, isterseniz yine de elle bir AI adayı işaretleyebilirsiniz.",
        )
        config["min_duration"] = st.number_input("Min süre (sn)", min_value=5, value=config.get("min_duration", 20))
        config["max_duration"] = st.number_input("Max süre (sn)", min_value=10, value=config.get("max_duration", 60))
        vertical_opts = ["blur_pad", "center_crop"]
        config["vertical_mode"] = st.selectbox(
            "Dikey mod", vertical_opts, index=vertical_opts.index(config.get("vertical_mode", "blur_pad"))
        )
    with col2:
        whisper_opts = ["base", "small", "medium", "large-v3"]
        current_whisper = config.get("whisper_model_size", "medium")
        config["whisper_model_size"] = st.selectbox(
            "Whisper model boyutu", whisper_opts,
            index=whisper_opts.index(current_whisper) if current_whisper in whisper_opts else 2,
        )
        config["whisper_language"] = st.text_input(
            "Whisper dil kodu (boş = otomatik algıla)", value=config.get("whisper_language") or ""
        ) or None
        config["gemini_model"] = st.text_input("Gemini modeli", value=config.get("gemini_model", "gemini-3.6-flash"))
        config["num_candidates"] = st.number_input(
            "Gemini aday sayısı", min_value=1, max_value=20, value=config.get("num_candidates", 8)
        )

    st.divider()
    st.subheader("Altyazı Stili (Pop Caption)")
    style = config.setdefault("subtitle_style", {})
    c1, c2, c3 = st.columns(3)
    with c1:
        style["font"] = st.text_input("Font", value=style.get("font", "Arial Black"))
        style["font_size"] = st.number_input("Font boyutu", min_value=20, max_value=200, value=style.get("font_size", 84))
        style["words_per_line"] = st.number_input("Öbekte en fazla kelime", min_value=1, max_value=6, value=style.get("words_per_line", 3))
    with c2:
        base_hex = st.color_picker("Taban renk (vurgusuz)", value=ass_to_hex(style.get("base_color", "&H00FFFFFF")))
        style["base_color"] = hex_to_ass(base_hex)
        highlight_hex = st.color_picker("Vurgu rengi", value=ass_to_hex(style.get("highlight_color", "&H0000D7FF")))
        style["highlight_color"] = hex_to_ass(highlight_hex)
        style["word_grow_percent"] = st.number_input("Büyüme oranı (%)", min_value=0, max_value=50, value=style.get("word_grow_percent", 15))
    with c3:
        position_opts = ["bottom_center", "middle_center", "top_center"]
        style["position"] = st.selectbox(
            "Konum", position_opts, index=position_opts.index(style.get("position", "bottom_center"))
        )
        style["margin_v_percent"] = st.number_input("Kenar boşluğu (%)", min_value=0, max_value=40, value=style.get("margin_v_percent", 12))
        style["phrase_gap_seconds"] = st.number_input(
            "Nefes molası eşiği (sn)", min_value=0.1, max_value=2.0, value=float(style.get("phrase_gap_seconds", 0.5)), step=0.1
        )

    st.divider()
    st.subheader("Giriş Efekti (Zoom + Ses)")
    st.caption("Her klibin başında kısa bir yakın çekimden normale açılan 'zoom punch' animasyonu, isteğe bağlı bir giriş sesi eşliğinde. Varsayılan olarak kapalıdır, yalnızca burada işaretlerseniz uygulanır.")
    zoom_cfg = config.setdefault("intro_zoom", {})
    z1, z2, z3 = st.columns(3)
    with z1:
        zoom_cfg["enabled"] = st.checkbox("Giriş efektini aktif et", value=zoom_cfg.get("enabled", False))
    with z2:
        zoom_cfg["zoom_start"] = st.number_input(
            "Başlangıç zoom oranı", min_value=1.0, max_value=2.0, value=float(zoom_cfg.get("zoom_start", 1.15)), step=0.05
        )
    with z3:
        zoom_cfg["duration_seconds"] = st.number_input(
            "Süre (sn)", min_value=0.1, max_value=3.0, value=float(zoom_cfg.get("duration_seconds", 0.5)), step=0.1
        )
    zoom_cfg["sound_effect_path"] = st.text_input(
        "Giriş ses efekti dosyası (opsiyonel, yoksa sadece görsel zoom uygulanır)",
        value=zoom_cfg.get("sound_effect_path", "assets/sfx/whoosh.mp3"),
    )

    st.divider()
    with st.expander("🏷️ Köşe Logosu (Watermark)"):
        st.caption("Her short'un köşesine isteğe bağlı bir logo bindirir. Kapalıyken hiçbir şey değişmez.")
        wm = config.setdefault("watermark", {})
        wm["enabled"] = st.checkbox("Köşe logosunu aktif et", value=wm.get("enabled", False))
        wm["logo_path"] = st.text_input(
            "Logo dosya yolu (PNG, şeffaf arka plan önerilir)", value=wm.get("logo_path", "assets/watermark_logo.png")
        )
        wm_c1, wm_c2, wm_c3 = st.columns(3)
        position_opts = ["bottom_right", "bottom_left", "top_right", "top_left"]
        with wm_c1:
            wm["position"] = st.selectbox(
                "Konum", position_opts, index=position_opts.index(wm.get("position", "bottom_right"))
            )
        with wm_c2:
            wm["width_percent"] = st.number_input(
                "Logo genişliği (video genişliğinin %'si)", min_value=5, max_value=50,
                value=int(wm.get("width_percent", 18)),
            )
        with wm_c3:
            wm["margin_percent"] = st.number_input(
                "Kenar boşluğu (%)", min_value=0, max_value=20, value=int(wm.get("margin_percent", 4))
            )
        if wm["enabled"] and not os.path.exists(os.path.join(PROJECT_DIR, wm["logo_path"])):
            st.warning(f"Logo dosyası bulunamadı: {wm['logo_path']} — aktif olsa da uygulanmayacak.")

    with st.expander("🖼️ Thumbnail Üretimi (Gemini görsel modeli — çoğu hesapta ücretsiz katmanda desteklenmez)"):
        thumb = config.setdefault("thumbnail", {})
        thumb["enabled"] = st.checkbox("Thumbnail üretimini aktif et", value=thumb.get("enabled", False))
        thumb["channel_name"] = st.text_input("Kanal adı", value=thumb.get("channel_name", ""))
        thumb["gemini_image_model"] = st.text_input(
            "Gemini görsel modeli", value=thumb.get("gemini_image_model", "gemini-3.1-flash-image")
        )
        thumb["logo_path"] = st.text_input("Logo dosya yolu (opsiyonel)", value=thumb.get("logo_path", "assets/logo.png"))

    with st.expander("📝 Başlık/Açıklama/Etiket Üretimi"):
        meta = config.setdefault("metadata", {})
        meta["enabled"] = st.checkbox("Metadata üretimini aktif et", value=meta.get("enabled", False))
        meta["youtube_search_enabled"] = st.checkbox(
            "YouTube'da benzer popüler videoları arayıp örnek al (önerilen)",
            value=meta.get("youtube_search_enabled", True),
            help=(
                "Açıksa, her klip için YouTube'da benzer konudaki popüler videolar aranır ve "
                "gerçek başlık/etiketleri Gemini'ye örnek olarak verilir (YOUTUBE_API_KEY gerekir). "
                "Arama başarısız olursa ya da anahtar girilmemişse otomatik olarak aşağıdaki "
                "proven titles listesine geri düşer."
            ),
        )
        meta["youtube_search_max_results"] = st.number_input(
            "Aranacak benzer video sayısı", min_value=5, max_value=30,
            value=int(meta.get("youtube_search_max_results", 15)),
        )
        proven_rel_path = meta.get("proven_titles_file", "config/proven_titles.txt")
        meta["proven_titles_file"] = st.text_input("Proven titles dosyası (yedek/manuel liste)", value=proven_rel_path)

        st.caption("YouTube araması kapalıysa veya başarısız olursa kullanılacak, kendi kanalınızdaki iyi başlıklar:")
        full_proven_path = os.path.join(PROJECT_DIR, proven_rel_path)
        proven_content = ""
        if os.path.exists(full_proven_path):
            with open(full_proven_path, "r", encoding="utf-8") as f:
                proven_content = f.read()
        new_proven_content = st.text_area("Proven titles içeriği", value=proven_content, height=150)
        if st.button("Proven Titles'ı Kaydet"):
            os.makedirs(os.path.dirname(full_proven_path) or ".", exist_ok=True)
            with open(full_proven_path, "w", encoding="utf-8") as f:
                f.write(new_proven_content)
            st.success("Kaydedildi.")

        st.divider()
        st.markdown("**🔄 Gerçek performansa göre otomatik doldur**")
        st.caption(
            "Elle küratörlük yerine, kanalınızın GERÇEK YouTube Analytics izlenme verilerine göre "
            "en çok izlenen videolarını çekip bu listeye yazar. credentials.json gerekir (SETUP.md) "
            "ve Google Cloud Console'da 'YouTube Analytics API'nin de etkinleştirilmiş olması gerekir."
        )
        an_c1, an_c2 = st.columns(2)
        with an_c1:
            an_top_n = st.number_input("Kaç video çekilsin", min_value=5, max_value=50, value=20, key="an_top_n")
        with an_c2:
            an_days = st.number_input("Son kaç gün", min_value=30, max_value=1825, value=365, key="an_days")

        if st.button("📊 Analytics'ten Çek", key="an_fetch_btn", type="primary"):
            try:
                with st.spinner("Analytics'ten gerçek izlenme verileri çekiliyor..."):
                    top_videos = pipeline.get_top_performing_videos(CONFIG_PATH, top_n=an_top_n, days=an_days)
                st.session_state.analytics_top_videos = top_videos
                if not top_videos:
                    st.warning("Hiç veri dönmedi (kanalda yeterli izlenme verisi olmayabilir).")
            except Exception as e:  # noqa: BLE001
                st.error(f"Çekilemedi: {e}")

        if st.session_state.get("analytics_top_videos"):
            top_videos = st.session_state.analytics_top_videos
            st.caption(f"{len(top_videos)} video bulundu (en çok izlenenden aza):")
            for v in top_videos:
                st.text(f"{v['views']:,} izlenme — {v['title']}".replace(",", "."))
            if st.button("Bu Listeyi Proven Titles Dosyasına Yaz (üzerine yazar)", key="an_apply_btn"):
                new_content = "\n".join(v["title"] for v in top_videos) + "\n"
                os.makedirs(os.path.dirname(full_proven_path) or ".", exist_ok=True)
                with open(full_proven_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                st.session_state.analytics_top_videos = None
                st.success("Proven titles, gerçek performans verisine göre güncellendi.")
                st.rerun()

    with st.expander("📤 YouTube'a Yükleme"):
        upload = config.setdefault("upload", {})
        upload["enabled"] = st.checkbox(
            "Yükleme özelliğini aktif et (sonuç galerisinde 'YouTube'a Yükle' butonu görünür)",
            value=upload.get("enabled", False),
        )
        upload["category_id"] = st.text_input("Kategori ID (20 = Gaming)", value=upload.get("category_id", "20"))
        privacy_opts = ["private", "public", "unlisted"]
        upload["privacy_status"] = st.selectbox(
            "Gizlilik durumu", privacy_opts, index=privacy_opts.index(upload.get("privacy_status", "private"))
        )
        schedule = upload.setdefault("schedule", {})
        schedule["start_delay_hours"] = st.number_input(
            "İlk yayına kaç saat kala", min_value=0, value=schedule.get("start_delay_hours", 1)
        )
        schedule["interval_hours"] = st.number_input(
            "Videolar arası saat", min_value=1, value=schedule.get("interval_hours", 6)
        )
        st.caption("Kurulum adımları için proje kökündeki SETUP.md dosyasına bakın.")

    if st.button("💾 Tüm Ayarları Kaydet", type="primary"):
        save_yaml_config(CONFIG_PATH, config)
        st.success("config.yaml güncellendi.")
