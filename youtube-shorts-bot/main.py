#!/usr/bin/env python3
"""YouTube -> Viral Shorts otomasyon pipeline'ı.

Kullanım:
    python main.py --url "https://www.youtube.com/watch?v=..."
    python main.py --url "..." --manual-clips "00:12-00:45,03:20-04:10"
    python main.py --fix-transcript VIDEO_ID
"""

import argparse
import glob
import json
import os
import sys
import time

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.analyzer import analyze_transcript
from src.burner import burn_subtitles
from src.clipper import cut_and_verticalize
from src.downloader import download_video
from src.subtitles import build_ass
from src.transcriber import (
    apply_corrections,
    load_transcript,
    parse_correction_line,
    save_transcript,
    transcribe,
)

DEFAULT_CONFIG = {
    "num_clips": 3,
    "min_duration": 20,
    "max_duration": 60,
    "vertical_mode": "blur_pad",
    "whisper_model_size": "medium",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "whisper_language": None,
    "gemini_model": "gemini-3.6-flash",
    "num_candidates": 8,
    "retry": {"max_retries": 5, "base_delay_seconds": 20},
    "output_resolution": {"width": 1080, "height": 1920},
    "subtitle_style": {},
    "intro_zoom": {"enabled": False},
    "thumbnail": {"enabled": False},
    "watermark": {"enabled": False, "logo_path": "assets/watermark_logo.png", "position": "bottom_right", "width_percent": 18, "margin_percent": 4},
    "metadata": {"enabled": False, "youtube_search_enabled": True, "youtube_search_max_results": 15},
    "upload": {"enabled": False, "auto_approve": False},
}


def log(stage: str, message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")


def load_config(path: str) -> dict:
    config = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        config.update(user_config)
    else:
        log("config", f"{path} bulunamadı, varsayılan ayarlar kullanılıyor.")
    return config


def words_in_range(words: list[dict], start: float, end: float) -> list[dict]:
    """[start, end] aralığına düşen kelimeleri, klip başlangıcına göre sıfırlanmış zamanla döndürür."""
    result = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        result.append(
            {
                "word": w["word"],
                "start": max(0.0, w["start"] - start),
                "end": min(end - start, w["end"] - start),
            }
        )
    return result


def segment_text_in_range(segments: list[dict], start: float, end: float) -> str:
    """[start, end] aralığına denk gelen whisper segmentlerinin metnini birleştirir."""
    parts = [seg["text"] for seg in segments if seg["start"] < end and seg["end"] > start]
    return " ".join(parts).strip()


def parse_time_to_seconds(value: str) -> float:
    """'SS', 'MM:SS' ya da 'HH:MM:SS' formatını saniyeye çevirir."""
    parts = value.strip().split(":")
    try:
        parts_f = [float(p) for p in parts]
    except ValueError as e:
        raise ValueError(f"Geçersiz zaman değeri: '{value}'") from e

    if len(parts_f) == 1:
        return parts_f[0]
    if len(parts_f) == 2:
        m, s = parts_f
        return m * 60 + s
    if len(parts_f) == 3:
        h, m, s = parts_f
        return h * 3600 + m * 60 + s
    raise ValueError(f"Geçersiz zaman formatı: '{value}' (beklenen: SS, MM:SS ya da HH:MM:SS)")


def parse_manual_clips(arg: str, video_duration: float) -> list[dict]:
    """'00:12-00:45,03:20-04:10' -> manuel klip sözlükleri listesi."""
    clips = []
    for i, chunk in enumerate(arg.split(","), start=1):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" not in chunk:
            raise ValueError(f"Geçersiz manuel klip aralığı: '{chunk}' (beklenen: MM:SS-MM:SS)")
        start_s, end_s = chunk.rsplit("-", 1)
        start = parse_time_to_seconds(start_s)
        end = parse_time_to_seconds(end_s)
        if end <= start:
            raise ValueError(f"Geçersiz manuel klip aralığı: '{chunk}' (bitiş başlangıçtan büyük olmalı)")
        end = min(end, video_duration)
        clips.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "title": f"Manuel Klip {i}",
                "reason": "Kullanıcı tarafından manuel olarak seçildi.",
                "score": None,
                "source": "manual",
            }
        )
    return clips


def process_clip(
    prefix: str,
    clip: dict,
    video_path: str,
    words: list[dict],
    segments: list[dict],
    output_dir: str,
    config: dict,
    proven_titles: list[str],
) -> dict:
    res = config["output_resolution"]
    width, height = res["width"], res["height"]

    raw_path = os.path.join(output_dir, f"{prefix}_raw.mp4")
    ass_path = os.path.join(output_dir, f"{prefix}.ass")
    final_path = os.path.join(output_dir, f"{prefix}.mp4")

    log("clipper", f"{prefix}: [{clip['start']:.1f}s - {clip['end']:.1f}s] kırpılıyor ve dikeye çevriliyor...")
    cut_and_verticalize(
        video_path,
        clip["start"],
        clip["end"],
        raw_path,
        width=width,
        height=height,
        mode=config["vertical_mode"],
        zoom_config=config.get("intro_zoom"),
    )

    log("subtitles", f"{prefix}: pop-caption altyazı (.ass) üretiliyor...")
    clip_words = words_in_range(words, clip["start"], clip["end"])
    build_ass(clip_words, ass_path, config.get("subtitle_style", {}), res_x=width, res_y=height)

    log("burner", f"{prefix}: altyazı videoya yakılıyor...")
    burn_subtitles(raw_path, ass_path, final_path, watermark_cfg=config.get("watermark"))
    # raw_path bilerek silinmiyor: --fix-transcript ile kırpma adımını tekrarlamadan
    # sadece altyazıyı yeniden üretip yeniden yakabilmek için saklanıyor.

    with open(os.path.join(output_dir, f"title_{prefix}.txt"), "w", encoding="utf-8") as f:
        f.write(clip["title"] + "\n")

    metadata = {
        "prefix": prefix,
        "source": clip["source"],
        "start": clip["start"],
        "end": clip["end"],
        "duration": round(clip["end"] - clip["start"], 2),
        "title": clip["title"],
        "reason": clip["reason"],
        "score": clip["score"],
        "source_video_path": video_path,
        "x_offset": 0.0,
        "scale": 1.0,
        "fade": {"enabled": False, "fade_in_seconds": 1.0, "fade_out_seconds": 1.0},
        "subtitle_position": None,
    }
    with open(os.path.join(output_dir, f"metadata_{prefix}.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    log("output", f"{prefix} tamamlandı -> {final_path}")

    entry = {
        "clip_dosyasi": final_path,
        "baslik": clip["title"],
        "aciklama": clip["reason"],
        "etiketler": [],
        "thumbnail_dosyasi": None,
    }

    thumb_cfg = config.get("thumbnail", {})
    meta_cfg = config.get("metadata", {})
    frame_path = None

    if thumb_cfg.get("enabled") or meta_cfg.get("enabled"):
        from src.thumbnail import extract_dramatic_frame

        frame_path = os.path.join(output_dir, f"{prefix}_frame.png")
        try:
            log("thumbnail", f"{prefix}: en dramatik kare aranıyor...")
            extract_dramatic_frame(
                video_path,
                clip["start"],
                clip["end"],
                frame_path,
                threshold=thumb_cfg.get("scene_change_threshold", 0.15),
            )
        except Exception as e:  # noqa: BLE001
            log("thumbnail", f"{prefix}: kare çıkarma başarısız, atlanıyor: {e}")
            frame_path = None

    if thumb_cfg.get("enabled") and frame_path:
        from src.thumbnail import generate_thumbnail

        thumb_path = os.path.join(output_dir, f"{prefix}_thumbnail.png")
        retry_cfg = config.get("retry", {})
        try:
            log("thumbnail", f"{prefix}: Gemini ile thumbnail üretiliyor...")
            generate_thumbnail(
                frame_path,
                thumb_path,
                clip["title"],
                clip["reason"],
                thumb_cfg.get("channel_name", ""),
                style_reference_paths=thumb_cfg.get("style_reference_images"),
                logo_path=thumb_cfg.get("logo_path"),
                model_name=thumb_cfg.get("gemini_image_model", "gemini-3.1-flash-image"),
                max_retries=retry_cfg.get("max_retries", 5),
                base_delay_seconds=retry_cfg.get("base_delay_seconds", 20),
            )
            entry["thumbnail_dosyasi"] = thumb_path
            log("thumbnail", f"{prefix}: thumbnail hazır -> {thumb_path}")
        except Exception as e:  # noqa: BLE001
            log("thumbnail", f"{prefix}: thumbnail üretimi başarısız, atlanıyor: {e}")

    if meta_cfg.get("enabled"):
        from src.metadata_writer import generate_metadata

        retry_cfg = config.get("retry", {})

        similar_videos = None
        if meta_cfg.get("youtube_search_enabled", True):
            youtube_api_key = os.environ.get("YOUTUBE_API_KEY")
            if youtube_api_key:
                try:
                    from src.youtube_search import find_similar_videos

                    log("youtube_search", f"{prefix}: benzer popüler videolar aranıyor...")
                    similar_videos = find_similar_videos(
                        clip["title"], youtube_api_key,
                        max_results=meta_cfg.get("youtube_search_max_results", 15),
                    )
                    log("youtube_search", f"{prefix}: {len(similar_videos)} benzer video bulundu.")
                except Exception as e:  # noqa: BLE001
                    log("youtube_search", f"{prefix}: arama başarısız, proven titles'a dönülüyor: {e}")

        try:
            log("metadata_writer", f"{prefix}: başlık/açıklama/etiket üretiliyor...")
            segment_text = segment_text_in_range(segments, clip["start"], clip["end"])
            result = generate_metadata(
                segment_text,
                clip["title"],
                clip["reason"],
                proven_titles,
                frame_path=frame_path,
                model_name=config["gemini_model"],
                max_retries=retry_cfg.get("max_retries", 5),
                base_delay_seconds=retry_cfg.get("base_delay_seconds", 20),
                similar_videos=similar_videos,
            )
            entry["baslik"] = result["title"]
            entry["aciklama"] = result["description"]
            entry["etiketler"] = result["tags"]
            log("metadata_writer", f"{prefix}: metadata hazır -> \"{result['title']}\"")
        except Exception as e:  # noqa: BLE001
            log("metadata_writer", f"{prefix}: metadata üretimi başarısız, klip başlığına dönülüyor: {e}")

    return entry


def generate_seo_suggestion(
    config_path: str,
    url: str | None = None,
    transcript_text: str | None = None,
    current_title: str = "",
    allow_audio_fallback: bool = True,
) -> dict:
    """Zaten yayınlanmış eski bir video için (link ya da elle yapıştırılmış transkriptten)
    başlık/açıklama/etiket ÖNERİSİ üretir. Kanala hiçbir şey yazmaz/yüklemez — sadece öneri
    döndürür, uygulamak kullanıcının elindedir.

    allow_audio_fallback=False verilirse ve YouTube altyazısı bulunamazsa, ses indirip Whisper
    çalıştırmak yerine net bir hata fırlatılır (bilgisayara hiçbir şey indirilmemesi istendiğinde
    kullanılır — bkz. GUI'deki tekil SEO aracı)."""
    load_dotenv()
    config = load_config(config_path)

    if not transcript_text:
        if not url:
            raise ValueError("Video linki ya da transkript metni gerekli.")

        from src.downloader import fetch_captions_text

        log("downloader", f"YouTube altyazıları deneniyor: {url}")
        captions = fetch_captions_text(url)
        if captions:
            transcript_text = captions["text"]
            if not current_title:
                current_title = captions.get("title", "")
            log("downloader", "YouTube altyazısı bulundu — ses indirme/Whisper atlanıyor (çok daha hızlı).")
        elif not allow_audio_fallback:
            raise RuntimeError(
                "Bu video için YouTube altyazısı bulunamadı. Bilgisayara ses/video indirmek "
                "istemediğiniz için işlem durduruldu — bunun yerine transkripti elle "
                "yapıştırabilirsiniz."
            )
        else:
            from src.downloader import download_audio_only

            log("downloader", "Altyazı bulunamadı, ses indiriliyor...")
            audio_info = download_audio_only(url, output_dir="downloads")
            if not current_title:
                current_title = audio_info["title"]

            log("transcriber", "Transkript çıkarılıyor (videonun uzunluğuna göre birkaç dakika sürebilir)...")
            transcript = transcribe(
                audio_info["path"],
                model_size=config["whisper_model_size"],
                language=config["whisper_language"],
                device=config["whisper_device"],
                compute_type=config["whisper_compute_type"],
            )
            transcript_text = " ".join(seg["text"] for seg in transcript["segments"]).strip()

    max_chars = 4000
    if len(transcript_text) > max_chars:
        transcript_text = transcript_text[:max_chars]

    meta_cfg = config.get("metadata", {})
    proven_titles = []
    proven_path = meta_cfg.get("proven_titles_file", "config/proven_titles.txt")
    if os.path.exists(proven_path):
        from src.metadata_writer import load_proven_titles

        proven_titles = load_proven_titles(proven_path)

    similar_videos = None
    if meta_cfg.get("youtube_search_enabled", True):
        youtube_api_key = os.environ.get("YOUTUBE_API_KEY")
        if youtube_api_key:
            try:
                from src.youtube_search import find_similar_videos

                log("youtube_search", "benzer popüler videolar aranıyor...")
                similar_videos = find_similar_videos(
                    current_title or transcript_text[:80], youtube_api_key,
                    max_results=meta_cfg.get("youtube_search_max_results", 15),
                )
            except Exception as e:  # noqa: BLE001
                log("youtube_search", f"arama başarısız, proven titles'a dönülüyor: {e}")

    from src.metadata_writer import generate_metadata

    retry_cfg = config.get("retry", {})
    log("metadata_writer", "başlık/açıklama/etiket önerisi üretiliyor...")
    result = generate_metadata(
        transcript_text,
        current_title or "Video",
        "Kanaldaki eski, SEO'su zayıf bir video için iyileştirme önerisi isteniyor.",
        proven_titles,
        model_name=config["gemini_model"],
        max_retries=retry_cfg.get("max_retries", 5),
        base_delay_seconds=retry_cfg.get("base_delay_seconds", 20),
        similar_videos=similar_videos,
    )

    return {
        "old_title": current_title,
        "title": result["title"],
        "description": result["description"],
        "tags": result["tags"],
    }


CHANNEL_SEO_INTER_VIDEO_DELAY_SECONDS = 2.0


def run_channel_seo_scan(
    config_path: str,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    force: bool = False,
    limit: int | None = None,
    video_type: str = "all",
) -> list[dict]:
    """Kimliği doğrulanmış kullanıcının kendi kanalındaki videoları tarayıp her biri için SEO
    önerisi üretir. Kanala hiçbir şey yazmaz/yüklemez — sadece öneri döndürür.

    video_type: "shorts" -> sadece kısa (≤3 dk) videolar, "long" -> sadece yatay/uzun videolar,
    "all" -> hepsi taranır; her durumda sadece
    herkese açık videolar dahil edilir (bkz. list_channel_videos). Sonuçlar
    output/_channel_seo/{video_id}.json içinde önbelleklenir; force=False ise daha önce işlenmiş
    videolar atlanır, böylece uzun bir tarama yarıda kesilirse kaldığı yerden devam edilebilir.

    Videolar arasına küçük bir bekleme konur — YouTube'un altyazı ucunda rate-limit (429)
    riskini azaltmak için (429 olursa sistem otomatik olarak ses indirip Whisper'a düşer, bu
    da hem yavaş hem de bilgisayara indirme demek)."""
    from src.uploader import list_channel_videos

    log("channel_seo", "Kanal videoları listeleniyor...")
    videos = list_channel_videos(credentials_path, token_path, max_results=limit, video_type=video_type)
    log("channel_seo", f"{len(videos)} video bulundu.")

    cache_dir = os.path.join("output", "_channel_seo")
    os.makedirs(cache_dir, exist_ok=True)

    results = []
    for idx, v in enumerate(videos, start=1):
        cache_path = os.path.join(cache_dir, f"{v['video_id']}.json")
        if not force and os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                results.append(json.load(f))
            log("channel_seo", f"[{idx}/{len(videos)}] {v['video_id']}: önbellekten alındı, atlanıyor.")
            continue

        if idx > 1:
            time.sleep(CHANNEL_SEO_INTER_VIDEO_DELAY_SECONDS)

        log("channel_seo", f"[{idx}/{len(videos)}] {v['video_id']} (\"{v['title']}\") işleniyor...")
        try:
            suggestion = generate_seo_suggestion(
                config_path,
                url=f"https://www.youtube.com/watch?v={v['video_id']}",
                current_title=v["title"],
            )
            result = {"video_id": v["video_id"], "error": None, **suggestion}
        except Exception as e:  # noqa: BLE001
            log("channel_seo", f"{v['video_id']}: HATA, atlanıyor: {e}")
            result = {"video_id": v["video_id"], "old_title": v["title"], "error": str(e)}

        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        results.append(result)

    return results


def get_top_performing_videos(config_path: str, top_n: int = 20, days: int = 365) -> list[dict]:
    """Kanalın GERÇEK YouTube Analytics izlenme verilerine göre en çok izlenen videolarını
    döndürür — proven_titles.txt'i elle küratörlük yerine gerçek performansa göre güncellemek
    için kullanılır. Hiçbir dosyayı kendisi değiştirmez, sadece veriyi döndürür."""
    config = load_config(config_path)
    upload_cfg = config.get("upload", {})
    from src.uploader import fetch_top_performing_videos

    return fetch_top_performing_videos(
        credentials_path=upload_cfg.get("credentials_path", "credentials.json"),
        token_path=upload_cfg.get("token_path", "token.json"),
        max_results=top_n,
        days=days,
    )


def local_video_id(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in base)


def analyze_source(
    url: str | None,
    config_path: str,
    manual_clips_arg: str | None = None,
    local_file: str | None = None,
) -> dict | None:
    """1-4. adımlar: indirme/yerel dosya + transkript + manuel klip ayrıştırma + Gemini analizi.

    Kırpma yapmaz — sadece adayları (ve manuel klipleri) döndürür, böylece kesime başlamadan önce
    (GUI'deki toplu iş kuyruğunda olduğu gibi) hangi klip(ler)in üretileceği seçilebilir.
    """
    load_dotenv()
    config = load_config(config_path)

    # 1. İndirme (ya da yerel dosya)
    if local_file:
        if not os.path.exists(local_file):
            log("downloader", f"HATA: Dosya bulunamadı: {local_file}")
            return None
        video_path = local_file
        video_id = local_video_id(local_file)
        log("downloader", f"Yerel dosya kullanılıyor: {video_path} (id={video_id})")
    else:
        log("downloader", f"İndiriliyor: {url}")
        try:
            video_info = download_video(url, output_dir="downloads")
        except RuntimeError as e:
            log("downloader", f"HATA: {e}")
            return None
        video_path = video_info["path"]
        video_id = video_info["video_id"]
        log("downloader", f"İndirildi: {video_path} (id={video_id}, başlık='{video_info['title']}')")

    # 2. Transkript
    log("transcriber", f"Transkript çıkarılıyor (model={config['whisper_model_size']})... Bu biraz sürebilir.")
    try:
        transcript = transcribe(
            video_path,
            model_size=config["whisper_model_size"],
            language=config.get("whisper_language"),
            device=config.get("whisper_device", "cpu"),
            compute_type=config.get("whisper_compute_type", "int8"),
        )
    except RuntimeError as e:
        log("transcriber", f"HATA: {e}")
        return None

    transcript_path = os.path.join("downloads", f"{video_id}_transcript.json")
    save_transcript(transcript, transcript_path)
    log(
        "transcriber",
        f"Transkript tamam: dil={transcript['language']}, "
        f"{len(transcript['segments'])} segment, {len(transcript['words'])} kelime -> {transcript_path}",
    )

    # 3. Manuel klipler (varsa)
    manual_clips = []
    if manual_clips_arg:
        try:
            manual_clips = parse_manual_clips(manual_clips_arg, transcript["duration"])
        except ValueError as e:
            log("manual-clips", f"HATA: {e}")
            return None
        log("manual-clips", f"{len(manual_clips)} manuel klip ayrıştırıldı.")

    # 4. Viral an analizi (Gemini)
    log("analyzer", f"Gemini ({config['gemini_model']}) ile viral anlar analiz ediliyor...")
    retry_cfg = config.get("retry", {})
    try:
        candidates = analyze_transcript(
            transcript["segments"],
            transcript["duration"],
            model_name=config["gemini_model"],
            min_duration=config["min_duration"],
            max_duration=config["max_duration"],
            num_candidates=config["num_candidates"],
            max_retries=retry_cfg.get("max_retries", 5),
            base_delay_seconds=retry_cfg.get("base_delay_seconds", 20),
        )
    except RuntimeError as e:
        log("analyzer", f"HATA: {e}")
        return None

    candidates.sort(key=lambda c: c["score"], reverse=True)
    for c in candidates:
        c["source"] = "ai"

    log("analyzer", f"{len(candidates)} aday bulundu:")
    for i, c in enumerate(candidates, start=1):
        log(
            "analyzer",
            f"  #{i} skor={c['score']} [{c['start']:.1f}s-{c['end']:.1f}s] \"{c['title']}\"",
        )

    return {
        "video_id": video_id,
        "video_path": video_path,
        "config": config,
        "transcript": transcript,
        "candidates": candidates,
        "manual_clips": manual_clips,
    }


def produce_clips(
    analysis: dict,
    selected_clips: list[dict],
    auto_approve: bool = False,
) -> dict:
    """5-10. adımlar: seçilen klip(ler)i kırpar, dikeye çevirir, altyazı yakar; opsiyonel olarak
    thumbnail/metadata üretir ve yükler. `selected_clips`, analyze_source'un döndürdüğü
    candidates + manual_clips listelerinden seçilmiş bir alt küme olmalı (her biri "source"
    alanı taşımalı: "ai" ya da "manual")."""
    config = analysis["config"]
    video_id = analysis["video_id"]
    video_path = analysis["video_path"]
    transcript = analysis["transcript"]

    output_dir = os.path.join("output", video_id)
    os.makedirs(output_dir, exist_ok=True)

    with open(os.path.join(output_dir, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump(analysis["candidates"], f, ensure_ascii=False, indent=2)

    meta_cfg = config.get("metadata", {})
    proven_titles = []
    if meta_cfg.get("enabled"):
        from src.metadata_writer import load_proven_titles

        proven_titles = load_proven_titles(meta_cfg.get("proven_titles_file", "config/proven_titles.txt"))

    ai_clips = [c for c in selected_clips if c.get("source") == "ai"]
    manual_clips = [c for c in selected_clips if c.get("source") == "manual"]
    to_process = [("ai", i, clip) for i, clip in enumerate(ai_clips, start=1)]
    to_process += [("manual", i, clip) for i, clip in enumerate(manual_clips, start=1)]

    success_count = 0
    entries = []
    for source, local_idx, clip in to_process:
        prefix = f"{source}_{local_idx}"
        try:
            entry = process_clip(
                prefix, clip, video_path, transcript["words"], transcript["segments"],
                output_dir, config, proven_titles,
            )
            entries.append(entry)
            success_count += 1
        except Exception as e:  # noqa: BLE001 - bir klip başarısız olsa bile diğerlerine devam et
            log("clipper", f"{prefix} işlenirken HATA oluştu, atlanıyor: {e}")

    log("done", f"{success_count}/{len(to_process)} short başarıyla üretildi -> {output_dir}/")

    if meta_cfg.get("enabled") and entries:
        from src.metadata_writer import save_aggregated_metadata

        agg_path = os.path.join(output_dir, "upload_metadata.json")
        save_aggregated_metadata(entries, agg_path)
        log("metadata_writer", f"Toplu metadata kaydedildi -> {agg_path}")

    upload_cfg = config.get("upload", {})
    if upload_cfg.get("enabled") and entries:
        from src.uploader import run_upload_flow

        run_upload_flow(
            entries,
            upload_cfg,
            auto_approve=upload_cfg.get("auto_approve", False) or auto_approve,
            credentials_path=upload_cfg.get("credentials_path", "credentials.json"),
            token_path=upload_cfg.get("token_path", "token.json"),
        )

    return {"video_id": video_id, "output_dir": output_dir, "entries": entries}


def run(
    url: str | None,
    config_path: str,
    manual_clips_arg: str | None = None,
    auto_approve: bool = False,
    local_file: str | None = None,
) -> dict | None:
    """CLI'nin kullandığı uçtan uca akış: analiz eder ve en iyi `num_clips` AI adayı + tüm manuel
    klipleri otomatik seçip hemen üretir (insan onayı/seçimi gerektirmez)."""
    analysis = analyze_source(url, config_path, manual_clips_arg=manual_clips_arg, local_file=local_file)
    if analysis is None:
        return None

    num_clips = analysis["config"]["num_clips"]
    selected = analysis["candidates"][:num_clips] + analysis["manual_clips"]
    return produce_clips(analysis, selected, auto_approve=auto_approve)


def resolve_video_id(arg: str) -> str:
    base = os.path.basename(arg.rstrip("/"))
    if base.endswith("_transcript.json"):
        base = base[: -len("_transcript.json")]
    elif base.endswith(".json"):
        base = base[: -len(".json")]
    return base


def print_clip_transcript_for_review(segments: list[dict], clip_start: float, clip_end: float) -> None:
    """Sadece bu klibe denk gelen kısmı, klibin kendi süresine göre (0:00 = klip başı) yazdırır."""
    print(f"\n=== Klip transkripti (0:00 = klibin başı, uzunluk {clip_end - clip_start:.1f}s) ===")
    for seg in segments:
        if seg["end"] <= clip_start or seg["start"] >= clip_end:
            continue
        rel_start = max(0.0, seg["start"] - clip_start)
        rel_end = min(clip_end - clip_start, seg["end"] - clip_start)
        print(f"[{rel_start:.2f}-{rel_end:.2f}] {seg['text']}")
    print("=======================================================\n")


def collect_corrections() -> list[dict]:
    print(
        "Düzeltmeleri şu formatta girin: 12.4-12.8: doğrusu \"Ahmet\"\n"
        "Birden fazla düzeltme art arda girebilirsiniz. Bitirmek için boş satır bırakın."
    )
    corrections = []
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if not line.strip():
            break
        parsed = parse_correction_line(line)
        if parsed is None:
            print('Format hatalı. Örnek: 12.4-12.8: doğrusu "Ahmet"')
            continue
        corrections.append(parsed)
    return corrections


def find_resumable_clips(output_dir: str) -> list[dict]:
    resumable = []
    for meta_path in sorted(glob.glob(os.path.join(output_dir, "metadata_*.json"))):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        prefix = meta.get("prefix")
        if not prefix:
            continue
        raw_path = os.path.join(output_dir, f"{prefix}_raw.mp4")
        if not os.path.exists(raw_path):
            continue
        resumable.append(
            {
                "prefix": prefix,
                "start": meta["start"],
                "end": meta["end"],
                "raw_path": raw_path,
                "ass_path": os.path.join(output_dir, f"{prefix}.ass"),
                "final_path": os.path.join(output_dir, f"{prefix}.mp4"),
                "source_video_path": meta.get("source_video_path"),
                "x_offset": meta.get("x_offset", 0.0),
                "scale": meta.get("scale", 1.0),
                "fade": meta.get("fade", {"enabled": False, "fade_in_seconds": 1.0, "fade_out_seconds": 1.0}),
                "subtitle_position": meta.get("subtitle_position"),
            }
        )
    return resumable


def update_clip_bounds(output_dir: str, prefix: str, new_start: float, new_end: float) -> None:
    """Bir klip GUI'deki süre kırpma sürgüsüyle kısaltıldığında metadata_{prefix}.json'daki
    start/end/duration alanlarını günceller (raw dosya zaten kırpılmış olmalı)."""
    meta_path = os.path.join(output_dir, f"metadata_{prefix}.json")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["start"] = round(new_start, 3)
    meta["end"] = round(new_end, 3)
    meta["duration"] = round(new_end - new_start, 3)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def cmd_fix_transcript(video_id_arg: str, config_path: str) -> None:
    video_id = resolve_video_id(video_id_arg)
    transcript_path = os.path.join("downloads", f"{video_id}_transcript.json")

    if not os.path.exists(transcript_path):
        log("fix-transcript", f"HATA: Transkript bulunamadı: {transcript_path}")
        return

    output_dir = os.path.join("output", video_id)
    resumable = find_resumable_clips(output_dir)
    if not resumable:
        log(
            "fix-transcript",
            f"{output_dir} içinde yeniden üretilebilecek short bulunamadı "
            "(ham klip dosyaları [*_raw.mp4] eksik olabilir).",
        )
        return

    resumable_by_prefix = {r["prefix"]: r for r in resumable}
    config = load_config(config_path)
    res = config["output_resolution"]
    width, height = res["width"], res["height"]

    while True:
        print(f"\n=== {video_id} için düzenlenebilir short'lar ===")
        for r in resumable:
            print(f"  {r['prefix']}  ({r['end'] - r['start']:.1f}s)")
        print("Düzeltmek istediğiniz short'un adını yazın (çıkmak için boş bırakın):")
        try:
            choice = input("> ").strip()
        except EOFError:
            choice = ""

        if not choice:
            log("fix-transcript", "Tamamlandı.")
            return

        clip = resumable_by_prefix.get(choice)
        if clip is None:
            print(f"'{choice}' bulunamadı. Geçerli isimler: {', '.join(resumable_by_prefix)}")
            continue

        # Klibi izleyip orada gördüğünüz saniyeye göre düzeltme girebilmeniz için transkript
        # ve düzeltme girişi bu klibin kendi süresine göre (0:00 = klip başı) gösterilir/alınır —
        # videonun tamamını (örn. 26 dk) taramanız gerekmez.
        transcript = load_transcript(transcript_path)
        print_clip_transcript_for_review(transcript["segments"], clip["start"], clip["end"])

        corrections = collect_corrections()
        if not corrections:
            print("Düzeltme girilmedi.")
            continue

        for c in corrections:
            c["start"] += clip["start"]
            c["end"] += clip["start"]

        transcript["words"] = apply_corrections(transcript["words"], corrections)
        save_transcript(transcript, transcript_path)
        log("fix-transcript", f"{len(corrections)} düzeltme uygulandı -> {transcript_path}")

        log("subtitles", f"{clip['prefix']}: düzeltilmiş transkriptle altyazı yeniden üretiliyor...")
        clip_words = words_in_range(transcript["words"], clip["start"], clip["end"])
        build_ass(clip_words, clip["ass_path"], config.get("subtitle_style", {}), res_x=width, res_y=height)

        log("burner", f"{clip['prefix']}: altyazı yeniden yakılıyor...")
        burn_subtitles(clip["raw_path"], clip["ass_path"], clip["final_path"])
        log("output", f"{clip['prefix']} güncellendi -> {clip['final_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube videosundan viral shorts üretir.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--url", help="YouTube video URL'si")
    mode.add_argument(
        "--local-file",
        metavar="PATH",
        help="İndirme yapmadan bilgisayardaki bir video dosyasını doğrudan işler.",
    )
    mode.add_argument(
        "--fix-transcript",
        metavar="VIDEO_ID",
        help="Var olan bir transkripti elle düzeltir ve ilgili short'ların altyazısını yeniden üretir "
        "(örn. --fix-transcript JNKvYAsThPQ ya da downloads/JNKvYAsThPQ_transcript.json).",
    )
    parser.add_argument("--config", default="config.yaml", help="Yapılandırma dosyası yolu")
    parser.add_argument(
        "--manual-clips",
        default=None,
        help='AI seçimine ek olarak işlenecek manuel zaman aralıkları, örn. "00:12-00:45,03:20-04:10".',
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Yükleme aktifse (upload.enabled) onay adımını atla ve doğrudan yükle.",
    )
    args = parser.parse_args()

    if args.fix_transcript:
        cmd_fix_transcript(args.fix_transcript, args.config)
    else:
        run(
            args.url,
            args.config,
            manual_clips_arg=args.manual_clips,
            auto_approve=args.auto_approve,
            local_file=args.local_file,
        )


if __name__ == "__main__":
    main()
