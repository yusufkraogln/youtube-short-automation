# Ek Prompt — Thumbnail, Başlık/Açıklama Üretimi ve Otomatik Yükleme

Bu, önceki mesajda kurduğun `youtube-shorts-bot` projesine **ek özellik** eklettirmek için Claude Code'a vereceğin ikinci prompt. Mevcut projeyi bozmadan üstüne eklemesini iste.

---

## PROMPT BAŞLANGICI

Az önce kurduğumuz `youtube-shorts-bot` projesine şu 3 ek özelliği ekle:

### 1. Otomatik thumbnail üretimi (`src/thumbnail.py`)
- Her kırpılan segment için Gemini'nin görsel üretim modelini (`gemini-2.5-flash-image` / "Nano Banana" ailesi) kullanarak kapak fotoğrafı üret
- Kanal ismim "[BURAYA KANAL ADINI YAZ]" ve League of Legends oynanış videoları paylaşıyorum. Thumbnail'ler şu kurallara uysun:
  - Segment içindeki en dramatik kareyi (ör. bir kill anı, outplay, komik tepki anı) referans al — bunun için ffmpeg ile segmentin ortasından/en yoğun anından bir kare (frame) çıkar, görsele referans olarak ver
  - Büyük, kontrast renkli, dikkat çekici metin overlay (örn. "1v5 OUTPLAY", "BUNU YAPMAZ", "?!" gibi merak uyandırıcı kısa ifadeler) — bu metni LLM'e transkriptten/segment içeriğinden ürettir
  - Kanal logom/watermark'ım varsa (`assets/logo.png` gibi bir yola koyacağım) sağ alt köşeye otomatik ekle
  - Config'te `thumbnail_style` altında referans örnek thumbnail'lerimin yollarını (`assets/thumbnail_examples/`) verebileyim, üretirken bu görsellerdeki stile (renk paleti, font, kompozisyon) benzemeye çalışsın
- Ücretsiz Gemini image tier limitlerine dikkat et, aynı rate-limit/retry mantığını burada da uygula

### 2. Kanıtlanmış başlıklardan öğrenen başlık/açıklama/etiket üretimi (`src/metadata_writer.py`)
- `config/proven_titles.txt` diye bir dosya oluştur, ben buraya kendi kanalımda gerçekten iyi performans göstermiş başlıkları (birer satır) elle ekleyeceğim
- Segment için başlık/açıklama/etiket üretirken, bu dosyadaki örnekleri LLM'e few-shot örnek olarak ver ("kanalımda geçmişte şu başlıklar iyi çalıştı, bu tarzda ve kalıpta yeni bir başlık üret" mantığıyla)
- LoL içeriğine özgü SEO etiketleri de otomatik ekle (şampiyon adı, rol, oyun modu gibi transkriptten/oyun içi görüntüden çıkarılabilecek bilgiler varsa)
- Çıktıyı tek bir metadata dosyasında topla: her segment için `{clip_dosyasi, baslik, aciklama, etiketler[], thumbnail_dosyasi}` — CSV veya JSON, hangisi kolaysa

### 3. YouTube'a otomatik yükleme/zamanlama (`src/uploader.py`) — OPSİYONEL, insan onaylı
- YouTube Data API v3 kullanarak videoyu kanalıma yükleyip zamanlayan bir modül ekle
- **Önemli:** Tam otomatik yayınlamak yerine, önce bana bir onay adımı sun — yani script'i çalıştırdığımda, yüklemeden önce üretilen tüm metadata + thumbnail'i terminalde özet olarak göstersin, ben "evet yükle" dedikten sonra yüklesin (`--auto-approve` flag'iyle bu adımı atlanabilir hale getir, ben istersem tam otomatik de çalıştırabileyim ama varsayılan onaylı olsun)
- Yükleme sırasında: başlık, açıklama, etiketler, thumbnail, ve yayın zamanı (config'te `upload_schedule`: örn. her X saatte bir) ayarlanabilsin
- YouTube Data API kurulumu için (Google Cloud Console'da proje açma, OAuth consent screen, credentials.json indirme) adım adım bana `SETUP.md` dosyasında talimat yaz — bunu ilk seferde ben elle yapacağım
- Günlük API kotası sınırlı olduğunu (varsayılan 10.000 birim/gün, bir video yüklemesi ~1600 birim tüketir, yani günde ~6 video yükleme sınırı var) README'ye not düş

### Genel
Bu üç özelliği mevcut pipeline'a (analyzer → clipper → subtitles → burner adımlarından sonra) doğal bir devam olarak ekle. `config.yaml`'a ilgili yeni ayarları ekle. Kurulum bittiğinde bana hangi dosyaları/klasörleri (logo, örnek thumbnail, proven_titles.txt) elle doldurmam gerektiğini listele.

## PROMPT SONU
