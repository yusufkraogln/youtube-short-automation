# youtube-shorts-bot

Bir YouTube video linkinden otomatik olarak dikey (9:16), "pop caption" tarzı kelime-kelime anlık
vurgulu (söylenen kelime sararıp büyür) altyazılı "viral shorts" klipleri üreten Python pipeline'ı.

Pipeline: **indir (yt-dlp) → transkript (faster-whisper, local) → viral an tespiti (Gemini, +
opsiyonel manuel klipler) → kırp + dikeye çevir (ffmpeg) → pop-caption altyazı yak (.ass + ffmpeg)**

## Kurulum

### 1. Sistem bağımlılıkları

**ffmpeg** sistemde kurulu olmalı:

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

### 2. Python bağımlılıkları

Python 3.10+ önerilir. Sanal ortam kullanmanız tavsiye edilir:

```bash
cd youtube-shorts-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Gemini API anahtarı

[Google AI Studio](https://aistudio.google.com/apikey) üzerinden ücretsiz bir API anahtarı alın,
sonra `.env` dosyası oluşturun:

```bash
cp .env.example .env
# .env dosyasını açıp GOOGLE_API_KEY=... satırını gerçek anahtarınızla doldurun
```

## Grafik arayüz (GUI)

Terminal komutlarıyla uğraşmak istemiyorsanız, aynı pipeline'ı tarayıcıda açılan bir arayüzden de
çalıştırabilirsiniz.

**Başlatmak için:** Finder'da `start_app.command` dosyasına çift tıklayın (ilk seferde macOS
"Bilinmeyen geliştirici" uyarısı verirse: dosyaya sağ tıklayıp **Aç**'ı seçin). Otomatik olarak
tarayıcıda bir sekme açılır.

Terminalden başlatmak isterseniz:
```bash
source .venv/bin/activate
streamlit run app.py
```

Arayüzde 3 sekme var:
- **🚀 Üretim** — Toplu (batch) kuyruk + kesmeden önce aday önizleme akışı:
  1. **Kuyruk**: Bir ya da daha fazla YouTube linki/dosya yolu ekleyin (her biri için isteğe
     bağlı manuel klip aralığı da verilebilir).
  2. **"🔍 Kuyruğu Analiz Et"** — kuyruktaki tüm videoları sırayla indirir/transkript eder/Gemini
     ile analiz eder (henüz hiçbir şey kesilmez). İlerleme canlı akar.
  3. **Aday İnceleme** — her video için Gemini'nin bulduğu tüm adaylar skor/başlık/gerekçesiyle
     listelenir, en iyi `num_clips` tanesi varsayılan olarak işaretlidir; istediğinizi
     işaretleyip istediğinizi kaldırabilirsiniz (manuel klipler her zaman dahildir).
  4. **"✂️ Seçilenleri Kes"** — sadece işaretlediğiniz adaylar kırpılıp altyazılanır; elenenler
     hiç işlenmez (zaman/disk kazandırır).
  5. Sonuçlar oynatıcılarla birlikte listelenir.
- **📂 Geçmiş Çıktılar** — Daha önce üretilmiş tüm videoları buradan açıp tekrar izleyebilir,
  düzenleyebilirsiniz.
- **⚙️ Ayarlar** — Gemini API anahtarı, tüm `config.yaml` ayarları (süre, whisper modeli, altyazı
  rengi/boyutu/büyüme oranı renk seçiciyle, thumbnail/metadata/upload açma-kapama) buradan
  düzenlenip kaydedilebilir. "Proven titles" listesi de burada elle güncellenebilir.

Her short'un altında **"✏️ Altyazı metnini düzenle"** kutusu bulunur — o klibin o anki altyazısı,
ekranda aynı anda çıkan öbekler satır satır kendi zaman aralığıyla birlikte gösterilir (örn.
`0.00-1.03: Merhaba ben enes`). Satır başındaki zamana dokunmadan sadece metni düzeltip "Düzenle
ve Yeniden Yak"a basmanız yeterli — değişmeyen kelimeler zamanlarını korur, sadece değiştirdiğiniz
kelime(ler) güncellenir. Video yeniden kırpılmadan sadece altyazısı güncellenir. Yükleme
özelliği `config.yaml`'da açıksa her short'un altında ayrı bir **"📤 YouTube'a Yükle"** butonu da
belirir — yükleme yalnızca bu butona bastığınızda, tek tek onayınızla gerçekleşir.

Her short'un altında ayrıca **"✂️ Süreyi kırp"** kutusu bulunur — iki uçtan da kısılabilen bir
sürgüyle klibin sadece istediğiniz kısmını (örn. 46 saniyelik bir klibin ilk 30 saniyesini)
kalıcı olarak kırpabilirsiniz; altyazı da yeni süreye göre otomatik yeniden üretilir. Video
yeniden indirilmez/kırpılmaz, mevcut ham klip üzerinden çalışır. **Bu işlem geri alınamaz**
(kırpılan kısım kalıcı olarak atılır), bu yüzden emin olmadan önce videoyu izleyin.

## Kullanım (terminal)

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
```

Farklı bir config dosyasıyla:

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --config config.yaml
```

### Bilgisayardaki bir video dosyasını kullanma (indirmeden)

`--url` yerine `--local-file` ile diskteki bir video dosyasını doğrudan işleyebilirsiniz —
indirme adımı tamamen atlanır:

```bash
python main.py --local-file "/Users/kullanici/Videolar/klip.mp4"
```

Video ID olarak dosya adı (uzantısız, özel karakterler `_` ile değiştirilmiş) kullanılır; transkript
ve çıktılar yine `downloads/` ve `output/{dosya_adi}/` altına yazılır.

### Manuel klip seçimi (AI seçimine ek olarak)

AI'ın kendi seçtiği segmentlere **ek olarak**, kendi belirlediğiniz zaman aralıklarını da aynı
çalıştırmada işletebilirsiniz (`SS`, `MM:SS` ya da `HH:MM:SS` formatında, virgülle ayrılmış):

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --manual-clips "00:12-00:45,03:20-04:10"
```

`--manual-clips` verilmezse sistem eskisi gibi sadece AI seçimiyle çalışır.

Çıktılar `output/{video_id}/` klasörüne yazılır. Dosya adları hangi short'un AI, hangisinin sizin
seçiminiz olduğunu ayırt eder (`ai_N` / `manual_N`):

```
output/{video_id}/
├── ai_1.mp4              # AI'ın seçtiği bitmiş dikey short (altyazı yakılmış)
├── ai_1_raw.mp4           # altyazısız ham klip (--fix-transcript için saklanır, silmeniz serbest)
├── ai_1.ass               # üretilen pop-caption altyazı dosyası (referans için)
├── title_ai_1.txt         # LLM'in önerdiği başlık
├── metadata_ai_1.json      # start/end/skor/gerekçe/source
├── manual_1.mp4            # sizin --manual-clips ile verdiğiniz short
├── manual_1_raw.mp4 ...
└── candidates.json         # Gemini'nin döndürdüğü tüm aday segmentler (sadece AI adayları)
```

### Transkript düzeltme (`--fix-transcript`)

Whisper bazı kelimeleri yanlış tanıyabilir. Videoyu yeniden indirip yeniden transkript etmeden,
sadece transkriptteki hatalı kelimeleri düzeltip ilgili short'ların altyazısını yeniden
üretebilirsiniz:

```bash
python main.py --fix-transcript VIDEO_ID
```

Orijinal videoyu (ör. 26 dakikalık kaynak) baştan izlemenize gerek kalmaz — sadece kısa short'u
izleyip orada gördüğünüz saniyeyi kullanırsınız:

Bu komut:
1. `output/{VIDEO_ID}/` içinde düzenlenebilir short'ları (henüz silinmemiş `*_raw.mp4` dosyası
   olanları) listeler; hangisini düzeltmek istediğinizi (örn. `ai_1`) yazarsınız.
2. Sadece **o klibe ait** transkript kısmını, **klibin kendi süresine göre** (0:00 = klibin başı,
   tıpkı short'u oynatırken gördüğünüz zaman gibi) terminalde gösterir.
3. Düzeltme girmenizi ister — format: `3.0-3.4: doğrusu "Ahmet"` (birden fazla satır art arda
   girilebilir, bitirmek için boş satır bırakın). Girdiğiniz zaman, o klip için geçerlidir —
   orijinal videonun neresinde olduğunu hesaplamanıza gerek yoktur.
4. Düzeltmeyi transkript JSON'una kalıcı olarak uygular ve **videoyu yeniden kırpmadan**, sadece
   o short'un altyazısını yeniden üretip yeniden yakar.
5. Başka bir short daha düzeltmek isteyip istemediğinizi sorar; boş satırla çıkarsınız.

⚠️ Bu akış `*_raw.mp4` dosyalarının silinmemiş olmasına bağlıdır — bu yüzden `process_clip` bu
dosyaları bilerek silmez. Bir short'u YouTube'a yükledikten ve düzeltmeye ihtiyacınız kalmadığına
karar verdikten sonra `*_raw.mp4` dosyalarını elle silebilirsiniz.

## Yapılandırma (`config.yaml`)

| Alan | Açıklama |
|---|---|
| `num_clips` | Kaç adet short üretilecek (varsayılan 3) |
| `min_duration` / `max_duration` | Hedeflenen segment süre aralığı (saniye) |
| `vertical_mode` | `blur_pad` (bulanık arka plan + ortada **kareye kırpılıp büyütülmüş** video — orijinal 16:9 bırakılsaydı ekranın çok küçük bir kısmını kaplardı) veya `center_crop` (merkezden kırp, tüm ekranı kaplar ama kenarlar kesilir) |
| `whisper_model_size` | `base` \| `small` \| `medium` \| `large-v3` — büyüdükçe daha doğru, daha yavaş (varsayılan `medium`; en iyi doğruluk için `large-v3` deneyin) |
| `whisper_language` | `null` = otomatik algıla, ya da `"tr"`, `"en"` gibi bir dil kodu |
| `gemini_model` | Kullanılacak Gemini modeli (varsayılan `gemini-3.6-flash`) |
| `subtitle_style` | Font, boyut, vurgu/taban renkleri, konum, öbek başına kelime sayısı (`words_per_line`, 2-3 önerilir), büyüme oranı (`word_grow_percent`), nefes molası eşiği (`phrase_gap_seconds`) |
| `output_resolution` | Çıktı çözünürlüğü (varsayılan 1080x1920) |
| `intro_zoom` | Her klibin başında kısa bir "zoom punch" giriş efekti: `enabled`, `zoom_start` (başlangıç zoom oranı, örn. 1.15), `duration_seconds` (efekt süresi), `sound_effect_path` (opsiyonel eşlik eden giriş sesi — dosya yoksa/boşsa sadece görsel zoom uygulanır) |

Varsayılan giriş sesi `assets/sfx/whoosh.mp3` — ffmpeg ile sentetik üretilmiş kısa bir "whoosh"
efektidir. Kendi ses efektinizi kullanmak isterseniz aynı yola bir dosya koymanız (ya da
`intro_zoom.sound_effect_path`'i değiştirmeniz) yeterli.

## Ek özellikler (opsiyonel): thumbnail, metadata, YouTube yükleme

Bu üç özellik varsayılan olarak **kapalıdır** (`config.yaml`'da her biri `enabled: false`),
temel pipeline'ı bozmadan üzerine eklenmiştir.

### Thumbnail üretimi (`thumbnail.enabled: true`)

Her klip için ffmpeg'in sahne değişimi (scene score) metriğiyle segmentteki en "hareketli" kare
bulunur, bu kare Gemini görsel üretim modeline (`gemini-3.1-flash-image`, config'ten değiştirilebilir)
referans olarak verilir ve dikkat çekici metin overlay'li bir thumbnail üretilir. `assets/logo.png`
varsa otomatik olarak sağ alt köşeye eklenir.

⚠️ Görsel üretim modelleri çoğu Gemini hesabında ücretsiz katmanda **desteklenmez** (test
sırasında `429 RESOURCE_EXHAUSTED ... limit: 0` hatası gözlemlendi — bu geçici bir rate-limit
değil, faturalandırma gerektiren kalıcı bir kısıtlamadır). `thumbnail.enabled: true` yapmadan
önce [Google AI Studio](https://aistudio.google.com/) / Cloud Console üzerinden projenizde
faturalandırmanın etkin olduğundan emin olun.

### Başlık/açıklama/etiket üretimi (`metadata.enabled: true`)

Varsayılan olarak (`metadata.youtube_search_enabled: true`) her klip için önce YouTube Data
API v3 ile benzer konudaki **gerçekten popüler videolar** aranır (`.env` → `YOUTUBE_API_KEY` —
salt-okunur arama olduğu için OAuth/`credentials.json` gerekmez, sadece Google Cloud Console'da
"YouTube Data API v3"ü etkinleştirip bir API anahtarı oluşturmanız yeterli); bulunan gerçek
başlık/etiketler Gemini'ye few-shot örnek olarak verilir. `YOUTUBE_API_KEY` girilmemişse ya da
arama başarısız olursa otomatik olarak `config/proven_titles.txt` dosyasındaki (kendi
ekleyeceğiniz) geçmişte iyi performans göstermiş başlıklara geri düşülür. Tüm segmentlerin
sonucu `output/{video_id}/upload_metadata.json` dosyasında toplanır.

⚠️ YouTube Data API v3 kotası: bir `search.list` çağrısı 100 birim tüketir (günlük kota 10.000
birim ise klip başına ~1 arama olmak üzere günde yaklaşık 100 klip için yeterlidir).

### YouTube'a yükleme/zamanlama (`upload.enabled: true`)

YouTube Data API v3 ile videoyu kanalınıza yükler. **Varsayılan olarak insan onayı ister**:
üretilen başlık/açıklama/etiket/thumbnail terminalde özetlenir, "evet" demeden yükleme
başlamaz. Tam otomatik çalıştırmak için `--auto-approve` bayrağını kullanın ya da
`config.yaml` → `upload.auto_approve: true` yapın. Kurulum adımları için [SETUP.md](SETUP.md).

⚠️ YouTube Data API v3 günlük kotası varsayılan olarak **10.000 birim**dir; bir video yüklemesi
yaklaşık **1600 birim** tüketir, yani günde yaklaşık **~6 video** yükleyebilirsiniz.

### Elle doldurmanız gerekenler

| Dosya/klasör | Ne zaman gerekli | Açıklama |
|---|---|---|
| `config.yaml` → `thumbnail.channel_name` | `thumbnail.enabled: true` | Kanal isminizi yazın |
| `assets/logo.png` | opsiyonel (thumbnail) | Varsa sağ alt köşeye otomatik eklenir, yoksa atlanır |
| `assets/thumbnail_examples/*.png` | opsiyonel (thumbnail) | Stil referansı için örnek thumbnail'lerinizi koyup `config.yaml` → `thumbnail.style_reference_images` listesine yollarını ekleyin |
| `.env` → `YOUTUBE_API_KEY` | `metadata.youtube_search_enabled: true` (varsayılan) | Google Cloud Console'dan "YouTube Data API v3" için basit bir API anahtarı oluşturup ekleyin |
| `config/proven_titles.txt` | opsiyonel yedek (YouTube araması kapalı/başarısız olursa) | Kanalınızda iyi performans göstermiş başlıkları birer satır olarak ekleyin |
| `credentials.json` | `upload.enabled: true` | [SETUP.md](SETUP.md) adımlarıyla Google Cloud Console'dan indirin |

## Notlar

- İlk çalıştırmada `faster-whisper` seçtiğiniz modeli indirir (birkaç yüz MB - birkaç GB arası,
  boyuta göre değişir); bu tek seferlik bir indirmedir.
- Gemini ücretsiz katmanında dakika/gün limitine takılırsanız (429 hatası), sistem otomatik olarak
  artan bekleme süreleriyle yeniden dener (`retry` ayarları `config.yaml`'da).
- Bir klibin işlenmesi hata verirse (örn. ffmpeg hatası), pipeline durmaz; hatayı loglar ve
  diğer klipleri işlemeye devam eder.
- Yeni bir platform/en-boy oranı eklemek isterseniz (örn. kare 1:1), `src/clipper.py` içindeki
  `cut_and_verticalize` fonksiyonuna yeni bir `mode` dalı eklemeniz yeterli.
- Türkçe ve İngilizce (ve Whisper'ın desteklediği diğer diller) için çalışır; `whisper_language`
  `null` bırakılırsa dil otomatik algılanır.
