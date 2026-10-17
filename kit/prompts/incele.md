<!-- guardian:v2 -->
# GUARDIAN AŞAMA SONU İNCELEME

1. Son aşama çapasını bul: `git tag --list 'asama-*' --sort=-creatordate | head -1` (yoksa ilk
   commit). `git diff <çapa>..HEAD --stat` ile başla.
2. Yalnızca riskli görünen dosyaları aç (büyüyen, çok değişen, yeni klasör/katman, veri erişimi).
3. AGENTS.md, KARARLAR.md ve DURUM.md'ye göre bak: mimari sapma, kod tekrarı, büyüyen karmaşıklık,
   performans/ölçeklenme riski, 3–5 yıl içinde sorun çıkaracak kararlar ve **aşırı mühendislik**
   (gereksiz soyutlama, servis, katman, yapılandırma).
4. Çıktı: en fazla 5 bulgu, her biri tek satır + önerilen eylem. Bulguları DURUM.md'deki
   "Açık bulgular" bölümüne yaz (eskilerden çözülenleri sil). Ayrı rapor dosyası üretme.
5. Bulgu yoksa "Açık bulgular: (yok)" yaz ve bunu tek cümleyle söyle.
