"""YouTube Data API v3 arama ile benzer/popüler videoları bulup gerçek başlık ve etiket verisi çeker.

Sadece herkese açık verileri okuduğu için basit bir API anahtarıyla çalışır (OAuth gerekmez) —
Google Cloud Console'da "YouTube Data API v3"ü etkinleştirip bir API anahtarı oluşturmak yeterli.
"""


def find_similar_videos(query: str, api_key: str, max_results: int = 15) -> list[dict]:
    """query'ye en alakalı videoları arar, izlenme sayısına göre çoktan aza sıralı
    [{"title", "tags", "view_count"}, ...] döndürür (arama sonucu yoksa boş liste)."""
    from googleapiclient.discovery import build

    youtube = build("youtube", "v3", developerKey=api_key)
    search_resp = youtube.search().list(
        q=query, part="id", type="video", maxResults=max_results,
        order="relevance", relevanceLanguage="tr",
    ).execute()
    video_ids = [
        item["id"]["videoId"] for item in search_resp.get("items", []) if "videoId" in item.get("id", {})
    ]
    if not video_ids:
        return []

    videos_resp = youtube.videos().list(part="snippet,statistics", id=",".join(video_ids)).execute()
    results = []
    for item in videos_resp.get("items", []):
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        results.append(
            {
                "title": snippet.get("title", ""),
                "tags": snippet.get("tags", []),
                "view_count": int(stats.get("viewCount", 0)),
            }
        )
    results.sort(key=lambda r: r["view_count"], reverse=True)
    return results
