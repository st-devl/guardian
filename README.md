# Guardian V1

AI ile geliştirilen projelerde **statik import bağımlılıklarını ve modül sınırlarını**
mekanik olarak denetleyen, tek dosyalık, bağımlılıksız bir mimari bekçisi.

> Tüy gibi hafif, çelik gibi güçlü.

- Python 3.11+ standart kütüphanesi dışında hiçbir şeye ihtiyaç duymaz.
- Normal geliştirmede yalnızca değişen dosyalara bakar (tipik diff < 100 ms).
- Başarıda sessizdir. İhlalde kısa ve net konuşur.
- Kontrol edemediği bir şeyi asla PASS saymaz.

---

## İçindekiler

1. [Guardian ne yapar, ne yapmaz](#1-guardian-ne-yapar-ne-yapmaz)
2. [Bu repo nasıl düzenlenmiş](#2-bu-repo-nasıl-düzenlenmiş)
3. [Bir projeye kurulum (adım adım)](#3-bir-projeye-kurulum-adım-adım)
4. [guardian.toml referansı](#4-guardiantoml-referansı)
5. [Dil örnekleri (TS, Laravel, Python, Go)](#5-dil-örnekleri)
6. [Günlük kullanım](#6-günlük-kullanım)
7. [Çıkış kodları ve hata kodları](#7-çıkış-kodları-ve-hata-kodları)
8. [Kural değişikliği süreci](#8-kural-değişikliği-süreci)
9. [Guardian'ı güncelleme](#9-guardianı-güncelleme)
10. [Sorun giderme](#10-sorun-giderme)
11. [Bilinen sınırlar](#11-bilinen-sınırlar)

---

## 1. Guardian ne yapar, ne yapmaz

**Mekanik olarak denetler:**

| Kontrol | Örnek |
|---|---|
| `module_deps` | `donations` modülü yalnızca `users`, `payments` modüllerine bağlanabilir |
| `forbid_import` | `controllers/**` → `repositories/**` import'u yasak |
| Config bütünlüğü | Çakışan modül yolları, geçersiz TOML, yazım hatalı anahtarlar → ERROR |
| Fail-closed | Import'larını okuyamadığı dosya, eksik git geçmişi, çökme → ERROR (asla PASS değil) |

Desteklenen diller: **JS/TS** (`.js .jsx .mjs .cjs .ts .tsx .mts .cts`), **Python** (`.py .pyi`),
**PHP** (`.php`, `use` ve doğrudan FQCN dahil), **Go** (`.go`).

TypeScript'te `import type` ve `export ... from` / `export * from` de bağımlılık sayılır.

**Denetlemez (V1 garanti kapsamı dışında):**

- Runtime DI, reflection, string tabanlı çözümleme (`app('reports')`, `new $class`)
- Değişkenle yapılan dinamik import (`import(name)`, `require(x)`)
- İş mantığı, güvenlik, performans, kod tekrarı, stil

**Advisory** kurallar mekanik olarak kontrol edilmez. Yalnızca `context` çıktısıyla AI'a
hatırlatılır ve çıktıda her zaman `[advisory]` olarak işaretlenir.

---

## 2. Bu repo nasıl düzenlenmiş

```
GuardianV1/                 ← bu repo (Guardian'ın kaynağı)
├── README.md               ← bu dosya (hedef projeye KOPYALANMAZ)
└── .guardian/              ← hedef projeye kopyalanan klasörün tamamı
    ├── guardian.py         ← motor + CLI + selftest (projeden bağımsız)
    ├── GUARDIAN.md         ← AI ajanı için kısa talimat (projeden bağımsız)
    └── guardian.toml       ← Project Brain ŞABLONU (her projede yeniden yazılır)
```

- `guardian.py` ve `GUARDIAN.md` bütün projelerde **aynıdır**. Güncellemede bu ikisi değişir.
- `guardian.toml` **projeye özeldir**. Güncellemede bu dosyaya asla dokunulmaz.

---

## 3. Bir projeye kurulum (adım adım)

Aşağıdaki örneklerde hedef proje `~/code/myapp`, Guardian kaynağı ise `~/code/GuardianV1`.

### Adım 0: Gereksinimler

```bash
python3 --version   # 3.11 veya üstü
git --version
```

Proje bir git reposu olmalıdır. Guardian, git olmadan `ERROR G004` verir.

### Adım 1: `.guardian/` klasörünü kopyala

GitHub'dan:

```bash
cd ~/code/myapp
git clone --depth 1 https://github.com/st-devl/guardian.git /tmp/guardian
cp -R /tmp/guardian/.guardian ./
rm -rf /tmp/guardian
```

veya yerel kopyadan:

```bash
cp -R ~/code/GuardianV1/.guardian ~/code/myapp/
```

Guardian'ı git submodule olarak değil, **kopya (vendoring)** olarak kurun. Böylece
`guardian.py` projenin kendi PR ve review sürecinden geçer, CI'da ekstra checkout gerekmez
ve her projede hangi sürümün çalıştığı bellidir.

Kurulumdan hemen sonra motorun sağlam olduğunu doğrulayın:

```bash
python3 .guardian/guardian.py --selftest && echo OK
```

Çıktı yalnızca `OK` olmalıdır. Başka bir şey görürseniz kuruluma devam etmeyin.

### Adım 2: Onboarding (Project Brain'i AI çıkarır)

Kopyalanan `guardian.toml` henüz yapılandırılmamış bir iskelettir. Modül tanımlanana kadar
`check` ve `context` bilerek `ERROR G010` verir: boş bir Brain hiçbir şeyi kontrol etmez,
bu yüzden PASS sayılamaz.

Teknolojileri, kaynak dizinlerini, modülleri ve alias'ları **elle yazmazsınız.** AI ajanına
şunu söyleyin:

> Run `python3 .guardian/guardian.py onboard` and follow it.

Bu tek seferlik süreçte:

1. **AI tespit eder, size sormaz:** `package.json`, `tsconfig`/`jsconfig` `paths`,
   `composer.json` (PSR-4), `pyproject.toml`, `go.mod` dosyalarını ve dizin yapısını
   okuyarak dilleri, kaynak dizinlerini ve `[resolve]` prefix'lerini çıkarır.
2. **Modül taslağını yazar.** Başlangıçta her modül için `depends = []` olur ve hiç
   `forbid_import` kuralı eklenmez.
3. **`onboard` komutunu tekrar çalıştırır.** Komut bu kez gözlenen gerçek bağımlılık
   grafiğini basar. Grafik, normal kontrollerde kullanılan extractor'la üretilir; ek bir
   keşif motoru yoktur.
   ```
   OBSERVED (current state; counts = importing files; ! = not in depends):
   donations -> payments(12)!, shared(30)!
   reports -> donations(4)!
   users -> donations(1)!
   cycles: donations<->users
   unmapped code: src/misc(3)
   ```
4. **Mevcut durumu mimari niyetle karıştırmaz.** Gözlenen her bağımlılık bir gerçektir,
   ama onay değildir. AI, açık olanları (`feature -> shared` gibi) `depends` için önerir.
   Döngüler, `shared -> feature`, alt katmandan üst katmana ve feature'dan feature'a
   bağımlılıklar ile belirsiz her şeyi **size sorar**.
5. **Yalnızca koddan cevaplanamayan soruları tek bir kısa liste halinde sorar.**
6. **Cevaplarınızı uygular.** Onaylanan bağımlılıklar `depends` listesine girer. Reddedilen
   mevcut bağımlılıklar WARN olarak görünmeye devam eder. `forbid_import` ve `advisory`
   yalnızca sizin onayladığınız kurallar için eklenir.
7. **`check --all` ERROR vermeyene kadar düzeltir** ve son `guardian.toml`'u onayınıza sunar.

Onaydan sonra onboarding biter. Normal kontroller projeyi bir daha analiz etmez; yalnızca
değişen dosyalara bakar. Brain sonradan kural değişikliği sürecinden geçer
([bölüm 8](#8-kural-değişikliği-süreci)).

Onboarding sırasında AI `.guardian/guardian.toml` dosyasını yazar. Bu nedenle Adım 4'teki
`deny` izinlerini onboarding bittikten **sonra** ekleyin.

Brain'in yapısı: [bölüm 4](#4-guardiantoml-referansı), elle yazılmış örnekler:
[bölüm 5](#5-dil-örnekleri).

### Adım 3: AI ajanına Guardian'ı tanıt

Ajanın gerçekten okuduğu ana talimat dosyasına **tek satır** ekleyin. Claude Code için
`CLAUDE.md`, Codex ve diğerleri için `AGENTS.md`:

```markdown
Architecture rules: read .guardian/GUARDIAN.md before coding.
```

`GUARDIAN.md` bu satır olmadan okunmaz. Dosyanın var olması tek başına yeterli değildir.

### Adım 4: L1 hook'u bağla (Claude Code)

`.claude/settings.json` dosyasına şunu ekleyin (dosya varsa `hooks` ve `permissions`
anahtarlarını mevcut içerikle birleştirin):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "python3 \"$CLAUDE_PROJECT_DIR\"/.guardian/guardian.py hook" }
        ]
      }
    ]
  },
  "permissions": {
    "deny": [
      "Edit(.guardian/**)",
      "Write(.guardian/**)",
      "Bash(git push origin main*)",
      "Bash(git push * main*)",
      "Bash(gh pr merge*)",
      "Edit(.github/workflows/**)",
      "Write(.github/workflows/**)"
    ]
  }
}
```

Hook, ajan her turu bitirdiğinde değişen dosyaları kontrol eder:

| Sonuç | Hook davranışı |
|---|---|
| PASS | Sessiz. Ajan normal biter. |
| BLOCK | İhlal ajana geri verilir, ajan kodu düzeltir. |
| BLOCK (düzeltme denemesinden sonra hâlâ) | Ajan durur, ihlal size gösterilir. Çözüm büyük ihtimalle bir kural değişikliği gerektiriyordur. |
| ERROR | Ajan durdurulur, tekrar denemez. İnsan müdahalesi gerekir. |

Hook mekanizması olmayan ajanlarda L1'i ajana `check` komutunu çalıştırtarak kullanın.
Asıl enforcement her durumda CI'dadır.

### Adım 5: CI'ı bağla (L3, asıl enforcement)

`.github/workflows/guardian.yml`:

```yaml
name: guardian
on: pull_request
permissions:
  contents: read
jobs:
  guardian:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0          # merge-base için tam geçmiş şart
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Guardian (base engine + base rules)
        env:
          BASE: origin/${{ github.base_ref }}
        run: |
          # PR'daki guardian.py'ye güvenilmez: motor base branch'ten alınır.
          if git cat-file -e "$BASE:.guardian/guardian.py" 2>/dev/null; then
            git show "$BASE:.guardian/guardian.py" > "$RUNNER_TEMP/guardian.py"
          else
            echo "WARN: $BASE has no guardian.py (bootstrap PR); using PR engine"
            cp .guardian/guardian.py "$RUNNER_TEMP/guardian.py"
          fi
          python3 "$RUNNER_TEMP/guardian.py" check --base "$BASE"
```

`fetch-depth: 0` önemlidir. Shallow clone'da merge-base bulunamaz ve Guardian bilerek
`ERROR G005` verir; boş diff'i PASS saymaz.

CI şu şekilde çalışır:

- **Motor base'den gelir.** PR kendi `guardian.py`'sini değiştirerek (örneğin her zaman
  PASS döndürecek şekilde) kendisini doğrulayamaz. CI, base branch'teki güvenilir
  `guardian.py`'yi PR ağacının dışına (`$RUNNER_TEMP`) çıkarır ve onu proje kökünden
  çalıştırır. `.guardian/` dışında çalışan motor, proje kökü olarak bulunduğu dizini
  (current directory) kullanır.
- PR'daki kod, **base branch'teki (main)** `guardian.toml` kurallarıyla kontrol edilir.
  PR içinde kuralları gevşetmek ihlali geçirmez.
- PR `guardian.toml`'u değiştiriyorsa yeni kurallarla ayrıca **tam tarama** yapılır.
  Sıkılaştırılan bir kural mevcut eski kodu da yakalar. Çıktıda bu satırlar `[head]` ile
  işaretlenir.

### Adım 6: GitHub'da main'i koru

CI'ın gerçek enforcement olması için bu adım zorunludur. **Settings → Branches**
(veya **Rules → Rulesets**) altında `main` için:

- [x] Require a pull request before merging
- [x] Require status checks to pass → `guardian` job'unu seçin
- [x] Do not allow bypassing the above settings
- [x] Require review from Code Owners (ekip varsa)

`.github/CODEOWNERS`:

```
/.guardian/          @st-devl
/.github/workflows/  @st-devl
/.claude/            @st-devl
/CLAUDE.md           @st-devl
/AGENTS.md           @st-devl
```

**Tek kişilik projelerde:** GitHub kendi PR'ınızı onaylamanıza izin vermez. Bu durumda
koruma şunlara dayanır: ajanın `.guardian/` dosyalarını düzenleme, main'e push etme ve merge
etme yetkisinin olmaması (Adım 4'teki `deny` listesi) ve merge işlemini sizin yapmanız.

### Adım 7: Kalibrasyon haftası

Şablonda enforced kurallar `severity = "warn"` ile gelir. İlk hafta:

1. Guardian normal akışta çalışır ve ihlalleri WARN olarak raporlar (exit 0, akış durmaz).
2. Şunları not edin:
   - **false positive**: Guardian'ın yanlışlıkla ihlal saydığı doğru kod
   - **kaçan ihlal**: Guardian'ın görmediği gerçek sınır ihlali
3. Yanlış tanımlanmış modülleri ve kuralları düzeltin. Gerçek ve bilinçli legacy
   ihlaller için gerekçeli `except` girdileri ekleyin.
4. Sonuçlar güvenilir hale gelince kuralları `severity = "block"` yapın. Bu değişikliği
   ayrı bir PR ile yapın.

### Kurulum kontrol listesi

```
[ ] .guardian/ kopyalandı, --selftest sessiz geçiyor
[ ] Onboarding tamamlandı (guardian.py onboard), check --all ERROR vermiyor
[ ] CLAUDE.md / AGENTS.md içinde GUARDIAN.md referansı var
[ ] .claude/settings.json: Stop hook + deny izinleri
[ ] .github/workflows/guardian.yml eklendi (motor base'den alınıyor)
[ ] main korumalı, guardian required status check
[ ] CODEOWNERS eklendi
[ ] Kalibrasyon bitince severity = "block"
```

---

## 4. guardian.toml referansı

Bilinmeyen her anahtar `ERROR G002` verir. Bir yazım hatası (`target` yerine `targets`
gibi) bir kuralı sessizce devre dışı bırakamaz.

```toml
# Üst seviye anahtarlar ilk [tablo]'dan ÖNCE yazılmalıdır (TOML kuralı).
ignore = ["src/**/*.stories.tsx"]      # hiç analiz edilmeyecek dosyalar (glob)

[project]
name = "myapp"

[resolve]                              # import prefix -> repo yolu, en uzun prefix kazanır
js  = { "@/" = "src/" }
php = { 'App\' = "app/" }              # ters bölü için tek tırnak (literal string)
go  = { "github.com/acme/app/" = "" }
py  = { "" = "" }                      # varsayılan: mutlak import'lar repo kökünden

[modules.donations]
paths   = ["src/donations/**"]         # yalnızca "<dizin>/**" biçimi
depends = ["users", "payments"]        # zorunlu; bağımlılık yoksa []

[[rules]]                              # isteğe bağlı: module_deps'in severity/except ayarı
id       = "DEPS"
type     = "module_deps"
severity = "block"

[[rules]]
id       = "ARCH-01"
type     = "forbid_import"
from     = ["src/**/controllers/**"]   # bu dosyalardan...
targets  = ["src/**/repositories/**"]  # ...bu hedeflere import yasak
severity = "block"                     # varsayılan block; "warn" akışı durdurmaz
msg      = "Controller -> service kullan"
except   = [{ path = "src/legacy/old.ts", reason = "Pre-Guardian legacy" }]

[[rules]]
id      = "TENANT-03"
type    = "advisory"
modules = ["donations", "payments"]    # modules yoksa global (en fazla ~5 tane)
msg     = "Payment account tenant'a aittir"
```

### Modüller

- `paths` yalnızca `<düz dizin>/**` biçiminde olabilir (`src/donations/**`). Dizin kısmında
  wildcard kullanılamaz.
- İki modülün dizinleri aynıysa veya iç içeyse `ERROR G003` verilir. Bir dosya asla iki
  modüle ait olamaz.
- `module_deps` modül tanımlanan her projede **her zaman açıktır**. `[[rules]]` altındaki
  `type = "module_deps"` kaydı yalnızca `id`, `severity` ve `except` ayarlamak içindir.
  Kayıt yoksa `id = "DEPS"` ve `severity = "block"` kullanılır.
- Modül dizinlerinin ilk segmentleri (örneğin `src`) "kök" sayılır. Bir kök altında olup
  hiçbir modüle ait olmayan kod dosyası `WARN W001` üretir.

### Import çözümleme

| Import | Nasıl çözülür |
|---|---|
| `./x`, `../x` (JS/TS) | Dosyanın dizinine göre |
| `from . import x`, `from ..a import b` (Python) | Paket dizinine göre |
| Diğer her şey | `[resolve]` tablosundaki en uzun eşleşen prefix ile |
| Hiçbir prefix'e uymayan | **Harici** kabul edilir (npm paketi, stdlib, vendor) ve yok sayılır |

Çözülen hedefler **uzantısız** yollardır (`src/users/repositories/repo`). Bu yüzden
`targets` glob'larını dizin bazlı yazın: `src/**/repositories/**`. `**/*Repository.ts`
çalışmaz.

`[resolve].php` ve `[resolve].go` kural kapsamında PHP veya Go dosyası varsa **zorunludur**.
Yoksa bütün import'lar harici görünür ve Guardian sessizce PASS vermek yerine `ERROR G007` verir.

### Glob semantiği

Yollar repo köküne göre, `/` ayraçlı ve büyük/küçük harfe duyarlıdır. Eşleşme tam
eşleşmedir.

| Desen | Anlamı | Eşleşir | Eşleşmez |
|---|---|---|---|
| `*` | tek segment içinde herhangi karakterler | `src/*` → `src/a.ts` | `src/a/b.ts` |
| `?` | tek karakter (`/` hariç) | `src/?.ts` → `src/a.ts` | `src/ab.ts` |
| `**` | sıfır veya daha fazla segment (yalnızca tam segment olarak) | `src/**` → `src`, `src/a/b.ts` | `srcx/a` |
| `**/x` | her derinlikte `x` | `**/*.ts` → `b.ts`, `a/b/c.ts` | `a/b.tsx` |

Şunlar `ERROR G002` verir: `[ ]`, `{ }`, `\`, `a**`, boş segment (`a//b`), `.` veya `..`
segmenti, baştaki veya sondaki `/`.

### Exceptions

- Her exception bir `reason` taşımak zorundadır.
- Bir exception glob'u bir modül kökünün kendisini veya bir modülün tamamını kapsayamaz
  (`src/donations`, `src/donations/**`, `src/**`, `**` → ERROR).
- Exception eklemek bir **kural değişikliğidir** ve CI'da base kuralları geçerli olduğu
  için ayrı bir PR ile gelmelidir.
- `check --all`, artık hiçbir ihlali kapsamayan exception'lar için `WARN W002` üretir.
- Kod içinde `// guardian-ignore` benzeri bir satır içi atlama mekanizması **yoktur**.

### Analiz edilmeyen dosyalar

- Bilinen kod dışı uzantılar otomatik atlanır: `.md .json .yaml .css .scss .html .svg`,
  görseller, fontlar vb.
- Kural kapsamındaki **bilinmeyen** uzantılar (`.swift`, `.kt`, `.vue`, `Dockerfile` vb.)
  `ERROR G006` verir. Import'ları yoksa `ignore` listesine ekleyin. Import'ları varsa
  Guardian bu dosyaları V1'de denetleyemez; bunu bilerek kabul etmelisiniz.

---

## 5. Dil örnekleri

### TypeScript / Next.js

```toml
ignore = ["src/**/*.test.ts", "src/**/*.stories.tsx"]

[resolve]
js = { "@/" = "src/" }

[modules.shared]
paths = ["src/shared/**", "src/components/ui/**"]
depends = []

[modules.users]
paths = ["src/features/users/**"]
depends = ["shared"]

[modules.donations]
paths = ["src/features/donations/**"]
depends = ["users", "shared"]

[modules.app]
paths = ["src/app/**"]
depends = ["users", "donations", "shared"]

[[rules]]
id = "UI-01"
type = "forbid_import"
from = ["src/components/**"]
targets = ["src/features/**"]
msg = "Ortak UI feature koduna bağlanamaz"
```

### Laravel (PHP, PSR-4)

```toml
[resolve]
php = { 'App\' = "app/" }

[modules.http]
paths = ["app/Http/**"]
depends = ["domain", "support"]

[modules.domain]
paths = ["app/Domain/**"]
depends = ["support"]

[modules.support]
paths = ["app/Support/**"]
depends = []

[modules.models]
paths = ["app/Models/**"]
depends = ["support"]

[[rules]]
id = "ARCH-01"
type = "forbid_import"
from = ["app/Http/Controllers/**"]
targets = ["app/Repositories/**"]
msg = "Controller -> service kullan"
```

PHP extractor şunları yakalar: `use` (grup, alias, `function` ve `const` dahil),
`new \App\X\Y()` gibi doğrudan FQCN kullanımı, namespace'e göre çözülen `Sub\Klass`,
trait `use` ve `#[\App\Attr]`. `"App\\X"` gibi string sınıf adları kapsam dışıdır.

`app/` kökü altındaki her dosya bir modüle ait olmalıdır. `app/Providers`,
`app/Console` gibi dizinler için de modül tanımlayın, aksi halde `W001` uyarısı alırsınız.

### Python

```toml
[resolve]
py = { "" = "" }      # paketler repo kökündeyse (varsayılan)
# py = { "" = "src/" } # src-layout kullanılıyorsa

[modules.users]
paths = ["myapp/users/**"]
depends = []

[modules.billing]
paths = ["myapp/billing/**"]
depends = ["users"]
```

### Go

```toml
[resolve]
go = { "github.com/acme/app/" = "" }   # go.mod'daki module path + "/"

[modules.users]
paths = ["internal/users/**"]
depends = []

[modules.payments]
paths = ["internal/payments/**"]
depends = ["users"]
```

---

## 6. Günlük kullanım

### Kontrol seviyeleri (L1 / L2 / L3)

| Seviye | Ne zaman | Ne çalışır | Guardian Core'un parçası mı? |
|---|---|---|---|
| **L1** | Her ajan turu sonunda | `guardian.py check` (değişen dosyalar) | Evet |
| **L2** | Commit / push gibi git checkpoint'leri | Projenin **kendi** lint, typecheck ve etkilenen test komutları | **Hayır** |
| **L3** | Pull request | `guardian.py check --base ...` (base motor + base kurallar) | Evet (asıl enforcement) |

**L2, Guardian Core'un zorunlu bir özelliği değildir.** Guardian lint, typecheck veya test
çalıştırmaz, bunların sonucunu yorumlamaz ve bunlar için bir kural tipi sunmaz. Guardian'ın
mekanik enforcement kapsamı **statik import bağımlılıkları ve mimari sınırlarla** sınırlıdır.

İsterseniz projenin mevcut komutlarını, projede zaten kullandığınız araçla (git hook,
husky, lefthook vb.) bir checkpoint'e bağlayabilirsiniz. Örnek `.git/hooks/pre-push`:

```sh
#!/bin/sh
set -e
npm run lint
npx tsc --noEmit
npm test -- --changedSince=origin/main
```

Bu komutlar tamamen projeye aittir. Guardian'ın PASS/BLOCK/ERROR sözleşmesinin parçası
değildir.

### Komutlar

```bash
# AI'a (veya kendinize) minimum mimari bağlam
python3 .guardian/guardian.py context donations
python3 .guardian/guardian.py context src/donations/controllers/x.ts

# L1: yalnızca değişen dosyalar (modified, staged, untracked, renamed, deleted)
python3 .guardian/guardian.py check

# Tam tarama (kalibrasyon, drift kontrolü)
python3 .guardian/guardian.py check --all

# L3: base kurallarıyla, merge-base'den itibaren değişenler
# (CI bunu base branch'teki motorla çalıştırır, bkz. Adım 5)
python3 .guardian/guardian.py check --base origin/main

# Tek seferlik onboarding (prosedür + gözlenen bağımlılık grafiği)
python3 .guardian/guardian.py onboard

# Motorun kendi doğrulaması
python3 .guardian/guardian.py --selftest
```

`context` çıktısı örneği (en fazla ~1500 karakter, aşılırsa `(+N omitted)`):

```
module donations: src/donations/**
depends [enforced DEPS]: users, payments, shared
ARCH-01 [enforced] Controller -> service kullan
TENANT-03 [advisory] Payment account tenant'a aittir
GLOBAL-01 [advisory, global] Her sorgu tenant_id ile filtrelenir
```

`check` çıktısı örneği (en fazla 10 satır, sonrası `+N more`):

```
BLOCK DEPS src/donations/api.ts:2 -> src/reports/r donations -> reports not in depends
BLOCK ARCH-01 src/donations/controllers/c.ts:1 -> src/users/repositories/repo Controller -> service kullan
```

Yerelde `guardian.toml` değiştiğinde `check` otomatik olarak tam taramaya geçer. Yeni bir
kuralın mevcut kodu nasıl etkilediğini hemen görürsünüz.

---

## 7. Çıkış kodları ve hata kodları

| Exit | Anlam | Kim çözer |
|---|---|---|
| `0` | PASS (WARN satırları olabilir) | Kimse |
| `1` | BLOCK: gerçek bir enforced ihlal | AI, uygulama kodunu düzeltir |
| `2` | ERROR: Guardian kontrolü güvenilir biçimde tamamlayamadı | İnsan |

Guardian'ın kendi çökmesi de `2` döner, asla `1` dönmez. Böylece bir çökme ihlal sanılıp
AI düzeltme döngüsüne girmez.

| Kod | Anlamı | Tipik çözüm |
|---|---|---|
| G000 | Python < 3.11 | Python'u yükseltin |
| G001 | `guardian.toml` okunamadı veya geçersiz | TOML sözdizimini düzeltin |
| G002 | Config şeması, glob veya komut hatası | Mesajdaki anahtarı düzeltin |
| G003 | Modül yolları çakışıyor | Dizinleri ayırın |
| G004 | git çalışmadı veya repo değil | Proje kökünü ve git kurulumunu kontrol edin |
| G005 | merge-base bulunamadı | CI'da `fetch-depth: 0` kullanın, base ref'i kontrol edin |
| G006 | Kural kapsamındaki dosya için extractor yok | `ignore` listesine ekleyin veya kapsamı daraltın |
| G007 | PHP veya Go için `[resolve]` eksik | `[resolve].php` / `[resolve].go` ekleyin |
| G008 | Dosya okunamadı | Dosya izinlerini kontrol edin |
| G009 | Guardian iç hatası | Hata mesajıyla birlikte raporlayın |
| G010 | Project Brain yapılandırılmamış (modül yok) | Onboarding: `guardian.py onboard` (Adım 2) |
| W001 | Kök altında modülsüz kod dosyası | Modül tanımlayın veya dosyayı taşıyın |
| W002 | Artık hiçbir ihlali kapsamayan exception | Exception'ı kaldırın |
| W003 | 5'ten fazla global advisory | Global bağlamı küçültün |
| W004 | Base branch'te yapılandırılmış Brain yok (kurulum/onboarding PR'ı); head kurallarıyla tam tarama yapılır | Normal; PR merge edilince kaybolur |

---

## 8. Kural değişikliği süreci

**Önce kural, sonra kod.** `donations` modülünün gerçekten `reports` modülüne bağlanması
gerekiyorsa:

1. AI bunu kodu kopyalayarak, dinamik import veya string lookup kullanarak ya da kodu
   modülsüz bir yere taşıyarak **dolanmaz**. Durur ve kural değişikliğini size önerir.
2. Siz kararı verirsiniz. `guardian.toml` değişikliği **yalnızca bu değişikliği içeren**
   ayrı bir PR ile main'e girer.
3. Uygulama kodu, yeni kurallar base'e girdikten sonraki bir PR'da gelir.

Aynı PR'da hem kuralı gevşetip hem kodu yazmak işe yaramaz. CI base kurallarını kullanır
ve BLOCK devam eder. Bu bir hata değil, bilinçli bir güvenlik davranışıdır.

---

## 9. Guardian'ı güncelleme

Motor güncellemesi (`guardian.py`) bir **enforcement değişikliğidir** ve kural değişikliği
gibi ayrı, korunan bir süreçten geçer:

- Güncelleme PR'ı **yalnızca** `.guardian/guardian.py` ve `.guardian/GUARDIAN.md`
  dosyalarını içerir. Uygulama kodu ve `guardian.toml` değişikliği içermez.
- PR'ı insan inceler ve merge eder (CODEOWNERS, Adım 6). AI ajanı `.guardian/`'ı
  düzenleyemez (Adım 4, `deny`).
- Bu PR'ın CI kontrolü hâlâ **eski (base) motorla** yapılır. Yeni motor ancak merge
  edildikten sonra, sonraki PR'larda devreye girer: önce motor, sonra kod.
- **`guardian.toml`'u asla ezmeyin.** O dosya projeye özeldir.

```bash
cd ~/code/myapp
git checkout -b chore/guardian-update
git clone --depth 1 --branch v1.x.y https://github.com/st-devl/guardian.git /tmp/guardian
cp /tmp/guardian/.guardian/guardian.py /tmp/guardian/.guardian/GUARDIAN.md .guardian/
rm -rf /tmp/guardian
python3 .guardian/guardian.py --selftest && python3 .guardian/guardian.py check --all
git commit -am "chore: update guardian to v1.x.y"   # sonra PR
```

Guardian sürümlerini bu repoda git tag ile işaretleyin (`v1.0.0`, `v1.0.1`, ...). Her
projenin hangi sürümü kullandığı, o projedeki commit mesajından anlaşılır.

---

## 10. Sorun giderme

**Hook hiç çalışmıyor.** `.claude/settings.json` içindeki komutu elle çalıştırın:
`echo '{}' | python3 .guardian/guardian.py hook; echo $?`. Claude Code'da `/hooks`
komutuyla hook'un kayıtlı olduğunu kontrol edin.

**Çok fazla `W001`.** Modül haritası eksik. Uyarıdaki dizinler için modül tanımlayın.
Kodun modüle ait olmaması gerekiyorsa (script'ler, config dosyaları) bu kodu modül
köklerinin dışına taşıyın veya `ignore` listesine ekleyin.

**False positive.** Önce hedef yolun nasıl çözüldüğüne bakın: çıktıdaki `-> hedef`
kısmı. Yanlış çözülüyorsa `[resolve]` prefix'ini düzeltin. Doğru çözülüyor ama izin
verilmesi gerekiyorsa `depends` listesine ekleyin (bu bir kural değişikliğidir).

**Kaçan ihlal.** Büyük ihtimalle import `[resolve]`'da olmayan bir alias ile yapılmıştır
(tsconfig `paths` içindeki `~reports/*` gibi) ve harici paket sanılmıştır. Alias'ı
`[resolve]` altına ekleyin.

**CI'da `G005`.** Checkout adımında `fetch-depth: 0` eksiktir.

---

## 11. Bilinen sınırlar

Guardian yapmadığı bir kontrolü yapıyormuş gibi görünmemelidir. Bu yüzden V1'in bilinen
sınırları burada açıkça listeleniyor:

- **Dinamik bağımlılıklar kapsam dışıdır.** `import(name)`, `require(x)`,
  `importlib.import_module(x)`, string sınıf adları ve DI container lookup'ları görülmez.
- **Modülsüz dosya yalnızca uyarı üretir.** Kök altındaki modülsüz bir dosya üzerinden
  dolaylı bağımlılık (`donations → misc/bridge → reports`) `W001` ile görünür ama BLOCK
  olmaz. Kalibrasyonda `W001` sayısını sıfıra indirin.
- **Workflow dosyası PR'dan gelir.** Motor ve kurallar base'den alınır, ancak GitHub
  `pull_request` workflow'unu PR'daki `.github/workflows/` içeriğiyle çalıştırır. Bir PR
  workflow'u değiştirerek Guardian adımını kaldırabilir. Bu yüzden `.github/workflows/`
  için CODEOWNERS ve ajan `deny` izinleri zorunludur (Adım 4 ve 6).
- **İlk kurulum PR'ında** base'de henüz `guardian.py` yoktur ve workflow PR'daki motoru
  kullanır (uyarı yazar). Bu PR'ı mutlaka insan incelemelidir.
- **L3 diff tabanlıdır.** Kural değişikliği içeren PR'larda tam tarama yapılır. Bunun
  dışında eşzamanlı merge'lerin çakışma riskine karşı branch korumasında "Require branches
  to be up to date before merging" seçeneğini açın.
- **Regex tabanlı extractor'lar** alışılmadık sözdizimlerini (Python satır devamı `\` ile
  bölünmüş import'lar, PHP şablonlarındaki HTML içi süslü parantezler gibi) kaçırabilir.
  Bu yüzden false positive ve kaçan ihlal sayıları Guardian'ın ana kalite metrikleridir.

Yeni özellik yalnızca gerçek kullanım verisi gerektirdiğinde eklenir.
