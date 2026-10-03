# Revizyon Prompt — Klip Seçimi, Altyazı Stili ve Transkript Düzeltme

Bu, çalışan `youtube-shorts-bot` projesine 3 revizyon yaptırmak için Claude Code'a vereceğin prompt.

---

## PROMPT BAŞLANGICI

Projede şu 3 revizyonu yap:

### 1. Klip seçimini yarı otomatik hale getir (manuel + AI birlikte)
Şu an sadece AI'ın seçtiği segmentler kırpılıyor. Bunu şöyle değiştir:
- CLI'ya opsiyonel bir `--manual-clips` argümanı ekle. Buradan `00:12-00:45,03:20-04:10` gibi virgülle ayrılmış zaman aralıkları girebileyim
- Girdiğim manuel aralıklar **AI'ın kendi seçtiği segmentlere ek olarak** işlensin — yani ikisi de aynı çalıştırmada üretilsin, biri diğerinin yerine geçmesin
- Çıktı klasöründe hangi short'un benim seçimim, hangisinin AI seçimi olduğu ayırt edilebilsin (örn. dosya adında `manual_1.mp4` / `ai_1.mp4` gibi bir önek, `metadata.json`'da da `source: "manual"` / `source: "ai"` alanı olsun)
- `--manual-clips` verilmezse sistem eskisi gibi sadece AI seçimiyle çalışmaya devam etsin (geriye dönük uyumlu olsun)

### 2. Altyazı stilini değiştir
Şu anki altyazı sistemi (kayan/akan renk geçişi, uzun cümle blokları) yerine şunu istiyorum:

**Segment uzunluğu:** Ekranda aynı anda en fazla 2-3 kelime görünsün. Bunu rastgele kelime sayısına göre değil, transkriptteki doğal cümle/nefes duraklarına (noktalama, cümle sonu, doğal duraklamalar) uygun şekilde böl — yani "Ahmet topu" / "aldı ve" gibi anlamsız yerlerden değil, mümkün olduğunca doğal kelime öbeklerinden kessin.

**Vurgu (highlight) mantığı:** Şu an akan/kayan bir renk geçişi var, bunun yerine **kelime-kelime anlık vurgu** istiyorum: Ekranda "Ahmet topu aldı" yazıyorsa ve o an "Ahmet" söyleniyorsa sadece "Ahmet" sarı, diğerleri (topu, aldı) beyaz olsun. Sıra "topu" kelimesine geldiğinde "topu" sarı olsun, "Ahmet" ve "aldı" beyaza dönsün. Yani her kelime SADECE kendi söylendiği süre boyunca sarı, öncesinde/sonrasında beyaz — kayan bir geçiş efekti değil, anlık açma/kapama gibi.

**Vurgulu kelimede büyüme efekti:** Sarı olan kelime aynı zamanda diğerlerine göre hafifçe (örneğin %10-15) büyüsün, göze çarpsın ama abartılı/rahatsız edici olmasın — kelime tekrar beyaza dönünce normal boyutuna dönsün.

Bu, TikTok/Reels'te yaygın olan "pop caption" tarzı — her kelime kendi sırası geldiğinde öne çıkıp sonra geri çekiliyor. İhtiyaç varsa altyazı üretim mantığını (her kelime için ayrı bir görüntüleme olayı oluşturarak) baştan kur.

### 3. Transkript doğruluğunu artırma + manuel düzeltme sistemi
Önce şunu dene: Whisper model boyutunu bir üst seviyeye çıkar (örn. `medium` kullanılıyorsa `large-v3`'e geç, config'ten seçilebilir yap) — bu çoğu yanlış tanımayı kendiliğinden azaltır.

Ama muhtemelen bazı kelimeler yine de yanlış çıkacak, bunun için bir düzeltme sistemi kur:
- Transkript çıkarıldıktan sonra, kelime zaman damgalarını da içeren düzenlenebilir bir ara dosya kaydet (örn. `transcript_TIMESTAMP.json`) — silinmesin, kalıcı olsun
- Yeni bir komut ekle: `python main.py --fix-transcript downloads/VIDEO_ID` gibi çalıştırdığımda bana transkripti (zaman damgalarıyla) terminalde göster, sonra düzeltme girmemi iste. Format basit olsun, örneğin: `12.4-12.8: doğrusu "Ahmet"` gibi bir satır yazarak o zaman aralığındaki kelimeyi düzeltebileyim. Birden fazla düzeltmeyi art arda girebileyim, bitince boş satırla onaylayayım
- Bu düzeltmeleri transcript JSON'una uygula, videoyu yeniden transkript ETMEDEN sadece o kelimeleri güncelle
- Düzeltilmiş transkriptle altyazıyı yeniden üret ve videoya yeniden yak (kırpma/analiz adımlarını tekrar çalıştırmaya gerek kalmasın, sadece altyazı üretim + yakma adımlarını yeniden çalıştır — zaman kazandırsın)
- Bu düzeltme adımını, henüz YouTube'a yüklenmemiş her short için ayrı ayrı yapabileyim

### Genel
Bu 3 değişikliği mevcut proje yapısını bozmadan, ilgili modülleri (`clipper.py`, `subtitles.py`, `transcriber.py`, `main.py`) güncelleyerek yap. Değişiklik sonrası `README.md`'yi de yeni komutlarla (`--manual-clips`, `--fix-transcript`) güncelle.

## PROMPT SONU
