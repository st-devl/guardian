<!-- guardian:v2 -->
# GUARDIAN GÜNCELLEME

`proje.md` değişti. Değişikliğin diff'i aşağıda (`.guardian/proje.son.md` → `proje.md`).
proje.md'yi baştan okuma; yalnızca diff'e ve etkilediği dosyalara bak.

1. Diff'in etkilediği yerleri bul: AGENTS.md maddeleri, guardian.toml ayarları (bağımlılıklar,
   adımlar, ithalat), DURUM.md görevleri. Etkilenmeyen hiçbir şeye dokunma.
2. Değişiklik bir KARARLAR.md kaydıyla çelişiyorsa kullanıcıya sor; cevabı yeni satır olarak ekle.
3. Önerilen değişiklikleri kısa bir liste olarak göster; **kullanıcı onayından sonra** yaz.
   guardian.toml değiştiyse KARARLAR.md'ye de bir satır ekle (pre-commit bunu ister).
4. `python3 .guardian/guardian.py doktor` çalıştır ve geçmeyen maddeleri düzelt. Yalnızca
   "senkron: proje.md değişti" maddesi kalırsa normaldir; oturum kapanınca `guardian` yeniler.

Sınırlar AGENTS.md 60, DURUM.md 80 satır. Rapor dosyası üretme.
