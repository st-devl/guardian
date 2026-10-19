# Guardian V2

Tek komutla kurulan, hafif bir mimari denetim sistemi. Tek başına, yapay zekâ ajanlarıyla
geliştirme yapanlar için: **Claude Code yönetir ve denetler, Codex kodu yazar**, Guardian
mekanik kuralları her turda, her commit'te ve CI'da modelden bağımsız uygular.

| Yapar | Yapmaz |
|---|---|
| `proje.md`'yi bir kez okutup projeye özel kuralları kurar | Her prompt'u sınıflandırmaz, kural yüklemez |
| İzinsiz bağımlılığı, şişen talimat dosyalarını, kırık lint/testi engeller | Rapor, log, pano üretmez |
| Değişmeyen ağacı yeniden kontrol etmez (önbellek) | Arka planda servis veya izleyici çalıştırmaz |
| Her parçanın gerçekten bağlı olduğunu `doktor` ile kanıtlar | Python standart kütüphanesi dışında bağımlılık istemez |
| İsteğe bağlı: import/modül sınırlarını denetler (`[ithalat]`) | Mantık, güvenlik veya stil incelemesi yapmaz |

İlke: kodla kontrol edilebilen kural talimat dosyasına değil kontrole gider; ihlal yoksa çıktı
yoktur; kontrol edemediği şeyi asla geçti saymaz.

## Bir kerelik kurulum

```sh
git clone --branch v2.0.1 https://github.com/st-devl/guardian.git ~/guardian
sh ~/guardian/install.sh        # `guardian` komutunu ~/.local/bin altına bağlar
```

Güncelleme (yeni sürüme geçip proje kökünde `guardian` yazın; motor kendini günceller):

```sh
git -C ~/guardian fetch --tags && git -C ~/guardian checkout v2.0.X
```

Gerekenler: Python 3.11+, `git`, [Claude Code](https://code.claude.com) (`claude`), Codex CLI (`codex`).

## Kullanım

1. Proje kökünde `proje.md` yazın (serbest format: amaç, yığın, mimari, aşamalar…).
2. Varsa `TASARIM/` klasörüne her ekranın HTML'ini ve görselini koyun
   (`TASARIM/giris/code.html` + `screen.png` veya `TASARIM/giris.html` + `giris.png`).
3. Proje kökünde:

```sh
guardian
```

Guardian kiti kopyalar, git ve Claude hook'larını bağlar, Claude Code'u açar. Claude `proje.md`'yi
bir kez okur, belirsiz kararları **tek seferde** sorar, `AGENTS.md`, `DURUM.md`, `KARARLAR.md` ve
`guardian.toml`'u onayınızla yazar. Kurulum ancak `doktor` 13/13 geçince tamamlanır; yarıda
kalırsa `guardian` yazmak kaldığı yerden devam ettirir.

Sonra `guardian` her çalıştırıldığında duruma göre karar verir:

| Durum | Ne olur |
|---|---|
| Kurulum yok / yarım | Kurulum / kaldığı yerden devam |
| Global Guardian daha yeni | Statik dosyalar (motor, CLAUDE.md, prompts, git hook) güncellenir ve commit edilir |
| `proje.md` değişti | Claude diff ile açılır; yalnızca etkilenen kurallar ve görevler güncellenir |
| `TASARIM/` değişti | `.guardian/tasarim.md` yenilenir, yeni/silinen ekranlar yazılır |
| Hiçbiri | Yalnızca `doktor` sonucu yazılır |

`AGENTS.md`, `DURUM.md`, `KARARLAR.md` ve `guardian.toml`'a güncellemede asla dokunulmaz.

### Günlük döngü

Claude, `DURUM.md`'den sıradaki görevi alır, riskini belirler (hafif / normal / kritik), brif
yazar, Codex'e verir, `kontrol` sonucuna ve diff'e bakar, `DURUM.md`'yi günceller ve sıradakine
geçer. Kritik kararlarda (şema, yeni bağımlılık, kimlik doğrulama, `guardian.toml`) durur ve sorar.

### `guardian incele`

Aşama sonunda çalıştırın. Claude, son `asama-N` tag'inden bu yana olan diff'e bakar; mimari sapma,
tekrar, karmaşıklık, performans ve aşırı mühendislik için en fazla 5 bulguyu `DURUM.md`'deki
"Açık bulgular" bölümüne yazar. Proje içindeyken `/incele` slash komutu da aynı işi yapar.

## Hedef projede kurulanlar

| Dosya | Rol |
|---|---|
| `CLAUDE.md` | Statik: rol, görev döngüsü, risk, brif, 5 soruluk diff incelemesi; `@AGENTS.md` ve `@DURUM.md`'yi içe aktarır |
| `AGENTS.md` | Claude ve Codex'in ortak anayasası (≤60 satır) |
| `DURUM.md` | Nerede olduğumuz (≤80 satır, üzerine yazılır) |
| `KARARLAR.md` | Verilen kararlar (karar başına tek satır, yalnızca eklenir) |
| `.claude/settings.json` | Stop hook + izinler (mevcut ayarlarla birleştirilir) |
| `.guardian/guardian.py` | Motorun proje içi kopyası; hook'lar ve CI buna bağlıdır |
| `.guardian/guardian.toml` | Projeye özel mekanik kurallar |
| `.guardian/git-hooks/pre-commit` | `kontrol --profil tam` + motor ve kural koruması |
| `.github/workflows/guardian.yml` | Yalnızca `origin` GitHub ise |

Var olan, Guardian işareti taşımayan `CLAUDE.md`/`AGENTS.md`/`settings.json` önce
`.guardian/yedek/<zaman>/` altına yedeklenir; hiçbir dosya sessizce ezilmez.

## `guardian.toml` kısa referans

```toml
[ajanlar]
gelistirici = "codex exec -s workspace-write -c sandbox_workspace_write.network_access=true -o .guardian/.cache/codex-son.md"

[bagimliliklar]            # izin listesi; listede olmayan paket = BLOCK
composer = ["laravel/framework", "pestphp/pest"]
npm = ["next", "react", "@types/*"]
# pub = [...]  pip = [...]

[limitler]                 # varsayılanlar
agents_md = 60             # boş satırlar hariç
claude_md = 80
durum_md = 80
karar_satir = 200

[[adim]]
ad = "lint"
komut = "vendor/bin/pint --test"
profil = "hizli"           # hizli: Stop hook, her tur (≤20 sn) · tam: hizli + testler
zaman_asimi = 120          # saniye

[ithalat]                  # isteğe bağlı import sınırı motoru (js/ts, py, php, go)
etkin = false
```

Bilinmeyen anahtar ERROR'dır: bir yazım hatası bir kuralı sessizce kapatamaz.
`guardian.toml`'u değiştiren commit, aynı commit'te `KARARLAR.md`'ye bir satır eklemelidir.

## Komutlar ve çıkış kodları

| Komut | Kim |
|---|---|
| `guardian` / `guardian incele` | Kullanıcı |
| `python3 .guardian/guardian.py kontrol [--profil hizli\|tam]` | Codex, Claude, hook'lar, CI |
| `python3 .guardian/guardian.py doktor` | `guardian`, Claude, kullanıcı |
| `python3 .guardian/guardian.py hook` | Claude Code Stop hook |

`0` PASS (çıktı yok) · `1` BLOCK (adım başına ≤10, toplam ≤30 satır) · `2` ERROR (Guardian
kontrol edemedi; çökme her zaman 2). Stop hook'ta BLOCK bir kez ajana geri verilir; düzeltme
denemesinden sonra hâlâ BLOCK ise ajan durur ve size gösterilir. ERROR'da ajan hemen durur.

| Kod | Anlam |
|---|---|
| G000 | Python 3.11+ gerekli |
| G001 | `guardian.toml` yok veya geçersiz TOML |
| G002 | Yapılandırma veya kullanım hatası (bilinmeyen anahtar dahil) |
| G003 | `[ithalat]` modül yolları çakışıyor |
| G004 | git çalışmadı |
| G006–G008 | `[ithalat]`: çıkarıcı yok / `resolve` eksik / dosya okunamadı |
| G009 | Guardian iç hatası |
| G013 | Adım aracı bulunamadı veya çalıştırılamadı |
| G014 | Adım zaman aşımına uğradı |
| G015 | Manifest (`composer.json`, `package.json`…) okunamadı |
| G016 | `proje.md` yok/boş/birden fazla; git kökü değil; kurulum iptal |
| G017 | `git`, `claude` veya `codex` eksik |
| G018 | Kurulum eksik (`durum.json`, talimat dosyası veya `settings.json`) |

## `doktor` ne kontrol eder

| # | Madde | Kanıt |
|---|---|---|
| 1 | kit | Kit dosyaları var, işaretli, statik dosyalar kurulu sürümle aynı |
| 2 | zincir | `CLAUDE.md` `@AGENTS.md` ve `@DURUM.md`'yi içe aktarıyor, hedefler var |
| 3 | yer-tutucu | `AGENTS.md`, `DURUM.md`, `guardian.toml`'da `{{…}}` kalmadı |
| 4 | claude-hook | `settings.json` geçerli; Stop hook motoru gösteriyor; allow/deny kuralları var |
| 5 | hook-calisiyor | Hook komutu `settings.json`'daki haliyle `{}` girdisiyle çalıştırılıyor ve protokole uygun yanıt veriyor |
| 6 | git-hook | `core.hooksPath` doğru; `pre-commit` çalıştırılabilir ve `kontrol` çağırıyor |
| 7 | kanarya | İzinsiz paketli sahte manifestler gerçekten BLOCK alıyor (`[ithalat]` etkinse onun kanaryası da) |
| 8 | adimlar | Her adımın aracı bulunuyor; `kontrol --profil hizli` geçiyor |
| 9 | ajanlar | `claude`, `codex` erişilebilir; geliştirici komutunun bayrakları kurulu sürümün `--help`'inde var |
| 10 | senkron | `proje.md` son işlenen sürümle aynı; `tasarim.md` güncel |
| 11 | limitler | Satır sınırları aşılmamış |
| 12 | ci | (etkinse) workflow `kontrol --profil tam` çağırıyor |
| 13 | gitignore | `.guardian/.cache/` yok sayılıyor |

## Sorun giderme

1. **`ERROR G013 … bulunamadı`**: adım aracı kurulu değil (`composer install`, `npm ci`) veya
   `guardian.toml`'daki komut yanlış. Guardian aracı olmayan adımı asla geçti saymaz.
2. **Claude her turda duruyor**: `kontrol --profil hizli` ERROR veriyor; komutu elle çalıştırıp
   mesajı okuyun. Hızlı profil 20 saniyeyi aşıyorsa testleri `tam` profile taşıyın.
3. **Commit reddedildi, "motor güncellemesiyle"**: `.guardian/guardian.py` veya git hook'u
   değişmiş. `git checkout -- .guardian/` ile geri alın; güncellemeyi `guardian` yapar.
4. **`doktor` "kit sürümünden farklı"**: statik bir dosya elle değişmiş. `git checkout -- <dosya>`.
5. **Kurulum yarım kaldı**: proje kökünde tekrar `guardian` yazın; kaldığı yerden devam eder.

## Bilinen sınırlar

- `git commit --no-verify` pre-commit'i atlar; CI (`origin` GitHub ise) yine `kontrol --profil tam` çalıştırır.
- Kontrol çalışma ağacını denetler; commit'e girmeyen değişiklikler de sonucu etkiler.
- Bağımlılık listesi yalnızca proje kökündeki manifestlere bakar (monorepo alt paketleri hariç).
- Tasarım envanteri dosya adlarına bakar; var olan bir ekranın içeriği değişirse bildirmez.
- `[ithalat]` yalnızca statik importları görür: çalışma anı DI, yansıma, string ile çözümleme kapsam dışıdır.
- Pre-commit POSIX `sh` gerektirir (macOS, Linux; Windows'ta Git Bash).

## Geliştirme

```sh
python3 guardian.py --selftest               # motor testleri (V1 import çıkarıcıları dahil)
python3 -m unittest discover -s tests -v     # uçtan uca testler (sahte Claude ile)
```
