<!-- guardian:v2 -->
# GUARDIAN KURULUM

Guardian bu projeye kopyalandı. Görevin projeye özel dosyaları doldurmak ve kurulumu
`doktor` geçene kadar tamamlamak. Sırayla uygula; bir adımı atlama.

1. **Oku (bir kez):** `proje.md`'yi (veya `PROJE.md`) baştan sona bir kez oku. Tasarım için
   yalnızca `.guardian/tasarim.md` envanterine bak; HTML ve görselleri bu aşamada açma.
   Mimariyi mevcut koddan çıkarma; kaynak proje.md'dir.
2. **Ayrıştır:** içeriği üçe ayır: mimari kurallar (her görevde geçerli), özellik tanımları
   (yalnızca ilgili görevde gerekli), durum bilgisi.
3. **AGENTS.md:** yalnızca `{{...}}` yer tutucularını doldur: `## Yığın`, `## Klasör yapısı ve
   katmanlar`, `## Değişmez ilkeler` (en fazla 12 madde). Yalnızca yargı gerektiren kurallar;
   kodla kontrol edilebilenler guardian.toml'a gider. Özellik detayı AGENTS.md'ye girmez.
   Genel kurallar bölümüne dokunma. Toplam en fazla 60 dolu satır.
4. **guardian.toml:** `[bagimliliklar]` izin listeleri (proje.md'deki ve iskeletin gerektirdiği
   paketler), yığına uygun `[[adim]]` tabloları (lint/tip kontrolü `hizli`, testler `tam`; hizli
   toplamı 20 sn'yi geçmesin), gerekiyorsa `[ithalat]` modül haritası. `{{ADIMLAR}}` satırını sil.
   `.github/workflows/guardian.yml` varsa kontrol adımından önce araç kurulum adımlarını ekle.
5. **DURUM.md:** proje.md'deki aşamalardan görev listesi. Her görev bir dikey dilim (bir ekran
   veya bir özellik, veriden arayüze). Tipik proje 15–25 görev; aşırı küçük bölme. En fazla 80 satır.
6. **Boşluk taraması:** proje.md'de net olmayan ve ileride her görevde farklı çözülebilecek
   kararları bul (ör. test/üretim veritabanı farkı, oturum yokken veri izolasyonu, tanımsız
   klasör yapısı, dosya adı tutarsızlıkları). **En fazla 10 soruyu tek seferde** sor; her soruda
   önerilen varsayılanı belirt. `AskUserQuestion` aracı varsa onu kullan. Her cevabı
   KARARLAR.md'ye tek satır yaz: `- K-001 · YYYY-MM-DD · <karar> — neden: <gerekçe>`.
7. **Onay:** önerilen AGENTS.md, guardian.toml ve DURUM.md özetini göster; **kullanıcı onayından
   sonra** yaz.
8. **İskelet:** kontrol adımlarının kullandığı araçlar projede yoksa (boş proje), ilk görevi
   Codex'e ver (`[ajanlar] gelistirici` komutu): proje iskeleti ve bu araçların kurulumu.
9. **Doktor:** `python3 .guardian/guardian.py doktor` çalıştır. Geçmeyen her maddeyi düzelt veya
   Codex'e düzelttir; geçene kadar tekrarla. 3 denemede geçmezse dur ve sorunu kullanıcıya göster.
10. **Bitiş:** doktor geçince `asama = aktif` olur (bunu doktor yazar; `.guardian/durum.json`'a
    dokunma). DURUM.md'deki ilk geliştirme görevine geçmeyi öner.

Kurallar: `.guardian/guardian.py`, `.guardian/git-hooks/` ve `.claude/settings.json`'u düzenleme.
Rapor veya log dosyası üretme. Kurulum bitene kadar Stop hook'u sessizdir; kontrolü sen çalıştır.
