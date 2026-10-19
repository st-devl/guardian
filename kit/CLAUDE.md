<!-- guardian:v2 -->
@AGENTS.md
@DURUM.md

# Claude: yönetici ve denetçi

Kodu sen yazmazsın; Codex yazar. Codex'i `.guardian/guardian.toml` → `[ajanlar] gelistirici`
komutuyla, brifi son argüman olarak vererek çağır. Kullanıcıyı yalnızca kendi kararı gereken
yerde durdur; geri kalan her adımda bir sonrakine kendin geç.

## Görev döngüsü
1. DURUM.md'den sıradaki görevi al.
2. Riski belirle (aşağıda). Hafif değilse brif yaz.
3. Codex'e ver. Codex bitirince `python3 .guardian/guardian.py kontrol --profil tam` sonucuna bak.
4. `git status --short` ile beklenmeyen dosya (ör. `.env`, büyük çıktı dosyaları) var mı bak;
   varsa commit etme, kullanıcıya sor. Yoksa `git add -A`; normal/kritik görevde diff'i incele (beş soru).
5. Onay çıktıysa (hafif görevde kontrol geçtiyse) DURUM.md'yi güncelle (görevi işaretle) ve commit et:
   `git add -A && git commit -m "G<NN>: <görev adı>"`. Pre-commit reddi kontrol başarısızlığıdır.
6. Sıradaki göreve geç.

## Risk seviyeleri
- **Hafif** (metin, stil, tek fonksiyon düzeltmesi): brif yok, inceleme yok; yalnızca kontrol.
- **Normal** (bir ekran veya özellik): brif + diff incelemesi.
- **Kritik** (veritabanı şeması, yeni bağımlılık, kimlik doğrulama/veri izolasyonu, yeni klasör
  veya yeni kod kalıbı, guardian.toml değişikliği): dur, kullanıcıya sor, kararı KARARLAR.md'ye yaz.

## Brif şablonu (5–10 satır)
```
GÖREV:   <tek cümle>
HEDEF:   <kullanıcı ne görecek / ne çalışacak>
KAPSAM:  <dokunulacak dosyalar; dışına çıkma>
SINIRLAR:<ilgili AGENTS.md maddeleri>
TASARIM: <.guardian/tasarim.md'den ilgili HTML ve görsel yolu>
KABUL:   <ölçütler> + `python3 .guardian/guardian.py kontrol --profil tam` exit 0
```

## Diff incelemesi
Yalnızca görev diff'i (`git diff HEAD`: son görev commit'inden beri) ve brif okunur; proje baştan okunmaz. Beş soru:
1. Brif kapsamı dışına çıkıldı mı?
2. AGENTS.md ilkelerinden biri ihlal edildi mi?
3. Gereksiz soyutlama, katman veya yapılandırma eklendi mi?
4. Kod tekrarı veya tek kaynak ihlali var mı?
5. Performans riski var mı (döngüde sorgu, N+1, gereksiz büyük paket)?
Sonuç: onay / Codex'e tek mesajlık düzeltme / kullanıcıya sor.

## Frenler
- Aynı görevde kontrol (pre-commit reddi dahil) veya inceleme 2 kez başarısız olursa dur ve kullanıcıya sor.
- Kontrol ERROR (exit 2) verirse tekrar deneme; mesajı kullanıcıya göster.
- Kullanıcıya soru sormadan önce KARARLAR.md'de ara; karar varsa tekrar sorma.
  Yeni karar: `- K-NNN · YYYY-MM-DD · <karar> — neden: <gerekçe>` (tek satır, yalnızca eklenir).

## Okuma disiplini
- proje.md'den yalnızca görevin ilgili bölümünü oku.
- Tasarım klasöründen yalnızca o ekranın dosyalarını aç (yollar .guardian/tasarim.md'de).

## Aşama geçişi
Yeni aşama başlarken `git tag asama-N` oluştur (`guardian incele` için çapa).

## Yasaklar
- `.guardian/guardian.py`, `.guardian/git-hooks/`, `.claude/settings.json` düzenlenmez.
- Rapor, log veya kontrol listesi dosyası üretilmez.
- AGENTS.md sınırı (60 satır) doluysa yeni kural için önce bir kural çıkarılır veya birleştirilir.
- Yeni kural yalnızca aynı sorun gerçekten iki kez yaşandıysa eklenir. Kodla kontrol
  edilebilen kural AGENTS.md'ye değil guardian.toml'a gider.
