<!-- guardian:v2 -->
# AGENTS.md — proje anayasası (Claude ve Codex için tek kaynak)

## Genel çalışma kuralları
- Görevi bitirmeden önce `python3 .guardian/guardian.py kontrol --profil tam` çalıştır; exit 0 olmadan "bitti" deme.
- exit 1 (BLOCK): uygulama kodunu düzelt. exit 2 (ERROR): dur, mesajı göster, tekrar deneme.
- Doğru çözüm yeni bir bağımlılık veya kural değişikliği gerektiriyorsa etrafından dolanma
  (kod kopyalama, dinamik import, string ile çözümleme yok); dur ve gerekçesini yaz.
- `.guardian/` ve `.claude/` altındaki dosyaları düzenleme.
- Brifteki kapsamın dışına çıkma; yalnızca brifte yazılı dosyalara dokun.
- Tasarımı olan ekranda yeni stil icat etme; brifteki HTML ve görsele uy.
- Basit ve yeterli çözümü seç; gelecekte lazım olabilir diye soyutlama, katman veya yapılandırma ekleme.
- Aynı mantığı ikinci kez yazma; var olanı kullan.
- Rapor, log veya not dosyası üretme.
- Mekanik kurallar (izinli bağımlılıklar, kontrol adımları) `.guardian/guardian.toml`'dadır; burada tekrarlanmaz.

## Yığın
{{YIGIN: dil, çatı, veritabanı, ön yüz, test aracı — her biri tek satır}}

## Klasör yapısı ve katmanlar
{{KLASORLER: klasör → sorumluluk; hangi katman hangisini çağırabilir}}

## Değişmez ilkeler
{{ILKELER: en fazla 12 madde; yalnızca yargı gerektiren kurallar}}
