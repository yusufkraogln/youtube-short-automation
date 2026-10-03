"""YouTube Data API v3 ile video yükleme/zamanlama. Varsayılan olarak insan onayı gerektirir.

Kurulum için SETUP.md dosyasına bakın (Google Cloud Console projesi, OAuth consent screen,
credentials.json).
"""

import datetime
import os
import re

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # youtube.upload SADECE video yükleme/güncelleme için yeterli — kanalın kendi videolarını
    # listelemek (channels.list/playlistItems.list, bkz. list_channel_videos) ayrı bir okuma
    # izni istiyor, aksi halde "insufficientPermissions" (403) hatası alınıyor.
    "https://www.googleapis.com/auth/youtube.readonly",
    # Kanalın gerçek izlenme verilerini okuyup proven_titles.txt'i buna göre güncellemek için
    # (bkz. fetch_top_performing_videos) — salt okunur, hiçbir şeyi değiştirmez.
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _load_credentials(credentials_path: str, token_path: str):
    # Bu bağımlılıklar sadece yükleme/analytics özellikleri aktifken gerekli olduğu için
    # burada import ediyoruz.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise RuntimeError(
                    f"{credentials_path} bulunamadı. Kurulum adımları için SETUP.md dosyasına bakın."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


def get_authenticated_service(credentials_path: str = "credentials.json", token_path: str = "token.json"):
    from googleapiclient.discovery import build

    creds = _load_credentials(credentials_path, token_path)
    return build("youtube", "v3", credentials=creds)


def get_analytics_service(credentials_path: str = "credentials.json", token_path: str = "token.json"):
    from googleapiclient.discovery import build

    creds = _load_credentials(credentials_path, token_path)
    return build("youtubeAnalytics", "v2", credentials=creds)


_ISO8601_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

# YouTube herkese açık bir "bu bir Short" alanı sunmuyor — Shorts'un güncel resmi süre sınırı
# (3 dakika) baz alınarak tahmin ediliyor. %100 kesin değil ama pratikte iyi çalışıyor.
SHORTS_MAX_SECONDS = 183


def _parse_iso8601_duration(value: str) -> int | None:
    if not value:
        return None
    m = _ISO8601_DURATION_RE.fullmatch(value)
    if not m:
        return None
    h, mnt, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mnt * 60 + s


def list_channel_videos(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    max_results: int | None = None,
    video_type: str = "all",
) -> list[dict]:
    """Kimliği doğrulanmış kullanıcının kendi kanalındaki videoları (id/başlık/tarih) listeler
    — kanalın 'yüklemeler' oynatma listesi üzerinden sayfalayarak.

    Sadece HERKESE AÇIK ve işlenmiş videolar döndürülür (private/unlisted/silinmiş/reddedilmiş
    olanlar otomatik elenir). video_type: "shorts" -> sadece ~3 dakika ve altı videolar,
    "long" -> sadece ~3 dakikayı aşan (yatay/uzun) videolar, "all" -> hepsi. max_results,
    FİLTRELENMİŞ sonuç sayısına uygulanır (gereksiz API çağrısını önlemek için filtreyi
    karşılayan yeterli video bulunca sayfalama erken durur)."""
    youtube = get_authenticated_service(credentials_path, token_path)

    channels_resp = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channels_resp.get("items", [])
    if not items:
        raise RuntimeError(
            "Kanal bulunamadı — OAuth ile giriş yaptığınız Google hesabının bir YouTube kanalı "
            "olduğundan emin olun."
        )
    uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos: list[dict] = []
    page_token = None
    while True:
        resp = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=page_token,
        ).execute()
        page_items = resp.get("items", [])
        if not page_items:
            break

        ids = [it["snippet"]["resourceId"]["videoId"] for it in page_items]
        title_by_id = {it["snippet"]["resourceId"]["videoId"]: it["snippet"].get("title", "") for it in page_items}
        published_by_id = {
            it["snippet"]["resourceId"]["videoId"]: it["snippet"].get("publishedAt", "") for it in page_items
        }

        details_resp = youtube.videos().list(part="status,contentDetails", id=",".join(ids)).execute()
        for v in details_resp.get("items", []):
            status = v.get("status", {})
            if status.get("privacyStatus") != "public" or status.get("uploadStatus") != "processed":
                continue  # gizli/liste-dışı/silinmiş/reddedilmiş -> atla

            duration_s = _parse_iso8601_duration(v.get("contentDetails", {}).get("duration", ""))
            is_short = duration_s is not None and duration_s <= SHORTS_MAX_SECONDS
            if video_type == "shorts" and not is_short:
                continue
            if video_type == "long" and is_short:
                continue

            vid = v["id"]
            videos.append(
                {
                    "video_id": vid,
                    "title": title_by_id.get(vid, ""),
                    "published_at": published_by_id.get(vid, ""),
                    "duration_seconds": duration_s,
                }
            )
            if max_results and len(videos) >= max_results:
                return videos

        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return videos


def fetch_top_performing_videos(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
    max_results: int = 20,
    days: int = 365,
) -> list[dict]:
    """YouTube Analytics API ile kanalın son `days` gündeki GERÇEK izlenme verilerine göre en
    çok izlenen videolarını döndürür (en çoktan aza sıralı, [{"video_id","title","views"}])
    — proven_titles.txt'i elle küratörlük yerine gerçek performansa göre güncellemek için."""
    import datetime

    analytics = get_analytics_service(credentials_path, token_path)
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days)

    report = analytics.reports().query(
        ids="channel==MINE",
        startDate=start_date.isoformat(),
        endDate=end_date.isoformat(),
        metrics="views",
        dimensions="video",
        sort="-views",
        maxResults=max_results,
    ).execute()

    rows = report.get("rows", [])
    if not rows:
        return []

    video_ids = [row[0] for row in rows]
    views_by_id = {row[0]: int(row[1]) for row in rows}

    youtube = get_authenticated_service(credentials_path, token_path)
    videos_resp = youtube.videos().list(part="snippet", id=",".join(video_ids)).execute()
    title_by_id = {item["id"]: item["snippet"]["title"] for item in videos_resp.get("items", [])}

    results = [
        {"video_id": vid, "title": title_by_id[vid], "views": views_by_id[vid]}
        for vid in video_ids
        if vid in title_by_id
    ]
    results.sort(key=lambda r: r["views"], reverse=True)
    return results


def compute_publish_time(index: int, start_delay_hours: float, interval_hours: float) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    publish_at = now + datetime.timedelta(hours=start_delay_hours + (index - 1) * interval_hours)
    return publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")


def upload_video(
    youtube,
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = "20",
    privacy_status: str = "private",
    publish_at: str | None = None,
    thumbnail_path: str | None = None,
) -> str:
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {"privacyStatus": privacy_status},
    }
    if publish_at and privacy_status == "private":
        body["status"]["publishAt"] = publish_at

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _status, response = request.next_chunk()

    video_id = response["id"]

    if thumbnail_path and os.path.exists(thumbnail_path):
        youtube.thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
        ).execute()

    return video_id


def print_upload_summary(entries: list[dict]) -> None:
    print("\n=== Yükleme Onayı Bekleniyor ===")
    for i, e in enumerate(entries, start=1):
        tags = e.get("etiketler", [])
        print(f"\n#{i} {e['clip_dosyasi']}")
        print(f"  Başlık: {e['baslik']}")
        print(f"  Açıklama: {e['aciklama'][:200]}")
        print(f"  Etiketler: {', '.join(tags[:10])}{'...' if len(tags) > 10 else ''}")
        print(f"  Thumbnail: {e.get('thumbnail_dosyasi', '-')}")
    print("\n================================\n")


def run_upload_flow(
    entries: list[dict],
    config: dict,
    auto_approve: bool = False,
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> list[dict]:
    if not entries:
        print("[uploader] Yüklenecek video yok, atlanıyor.")
        return []

    print_upload_summary(entries)

    if not auto_approve:
        answer = input("Bu videoları YouTube'a yüklemek istiyor musunuz? (evet/hayir): ").strip().lower()
        if answer not in ("evet", "e", "yes", "y"):
            print("[uploader] Yükleme iptal edildi.")
            return []

    try:
        youtube = get_authenticated_service(credentials_path, token_path)
    except RuntimeError as e:
        print(f"[uploader] HATA: {e}")
        return []

    schedule_cfg = config.get("schedule", {})
    start_delay_hours = schedule_cfg.get("start_delay_hours", 1)
    interval_hours = schedule_cfg.get("interval_hours", 6)
    privacy_status = config.get("privacy_status", "private")
    category_id = config.get("category_id", "20")

    uploaded = []
    for i, e in enumerate(entries, start=1):
        publish_at = compute_publish_time(i, start_delay_hours, interval_hours)
        try:
            video_id = upload_video(
                youtube,
                e["clip_dosyasi"],
                e["baslik"],
                e["aciklama"],
                e.get("etiketler", []),
                category_id=category_id,
                privacy_status=privacy_status,
                publish_at=publish_at,
                thumbnail_path=e.get("thumbnail_dosyasi"),
            )
            print(
                f"[uploader] Yüklendi: {e['clip_dosyasi']} -> "
                f"https://youtu.be/{video_id} (yayın zamanı: {publish_at})"
            )
            uploaded.append({**e, "video_id": video_id, "publish_at": publish_at})
        except Exception as ex:  # noqa: BLE001 - bir video başarısız olsa bile diğerlerine devam et
            print(f"[uploader] HATA ({e['clip_dosyasi']}): {ex}")

    return uploaded
