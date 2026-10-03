# SETUP.md — YouTube Data API v3 Kurulumu (otomatik yükleme için)

Bu adımlar yalnızca `config.yaml` içinde `upload.enabled: true` yaparsanız gereklidir. Thumbnail
ve metadata özellikleri bu kurulumu gerektirmez, sadece `GOOGLE_API_KEY` (Gemini) yeterlidir.

## 1. Google Cloud projesi oluşturun

1. [Google Cloud Console](https://console.cloud.google.com/) adresine gidin.
2. Üstteki proje seçiciden **New Project** ile yeni bir proje oluşturun (örn. "youtube-shorts-bot").

## 2. Gerekli API'leri etkinleştirin

1. Sol menüden **APIs & Services → Library** açın.
2. "YouTube Data API v3" araması yapın, açın ve **Enable** tıklayın (yükleme + kanal tarama için).
3. "YouTube Analytics API" araması yapın, açın ve **Enable** tıklayın (gerçek izlenme verisine
   göre proven titles güncelleme özelliği için — kullanmayacaksanız atlayabilirsiniz).

## 3. OAuth consent screen (izin ekranı) yapılandırın

1. **APIs & Services → OAuth consent screen** açın.
2. User Type olarak **External** seçin (kişisel Google hesabınızla kullanacaksanız).
3. Uygulama adı, destek e-postası gibi zorunlu alanları doldurun.
4. **Scopes** adımında şu iki kapsamı ekleyin:
   - `https://www.googleapis.com/auth/youtube.upload` (yükleme + kanal tarama)
   - `https://www.googleapis.com/auth/yt-analytics.readonly` (gerçek izlenme verisi — salt okunur)
5. **Test users** adımında yükleme yapacağınız YouTube hesabının Google e-postasını ekleyin
   (uygulama "Testing" modundayken sadece burada eklenen hesaplar giriş yapabilir).

## 4. OAuth Client ID (credentials.json) oluşturun

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Desktop app** seçin, bir isim verin, oluşturun.
3. Oluşan client için **Download JSON** tıklayın.
4. İndirilen dosyayı proje kök dizinine `credentials.json` adıyla kaydedin
   (`youtube-shorts-bot/credentials.json`).

> `credentials.json` ve sonradan üretilecek `token.json` dosyalarını **asla** paylaşmayın veya
> git'e eklemeyin — `.gitignore` dosyasına bu iki dosyayı da ekleyin.

## 5. İlk yükleme ve tarayıcı onayı

`upload.enabled: true` iken `python main.py --url "..."` çalıştırdığınızda, ilk yükleme
denemesinde bir tarayıcı penceresi açılıp Google hesabınızla giriş yapmanızı ve izin vermenizi
isteyecektir. Onayladıktan sonra bir `token.json` dosyası oluşur ve sonraki çalıştırmalarda
tekrar tarayıcı açılmaz (token otomatik yenilenir).

## Günlük kota notu

YouTube Data API v3 varsayılan günlük kota **10.000 birim**dir. Bir video yüklemesi
(`videos.insert`) yaklaşık **1600 birim** tüketir — yani varsayılan kotayla günde yaklaşık
**~6 video** yükleyebilirsiniz. Kotayı artırmak isterseniz Cloud Console'dan "Quotas" bölümünden
artış talebinde bulunabilirsiniz (onay süresi alabilir).

## Sorun giderme

- **"Access blocked: this app's request is invalid"**: OAuth consent screen'de scope eksik ya da
  test kullanıcı listesine hesabınızı eklemediniz.
- **`credentials.json bulunamadı`**: Dosyanın proje kök dizininde ve tam olarak bu isimde
  olduğundan emin olun, ya da `config.yaml` → `upload.credentials_path` ile farklı bir yol verin.
- **`invalid_grant` / token hatası**: `token.json` dosyasını silip yeniden çalıştırın, tarayıcı
  onay akışı yeniden başlar.
