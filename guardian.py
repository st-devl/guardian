#!/usr/bin/env python3
"""Guardian V2: tek komutla kurulan hafif mimari denetim.

Kullanım:
  guardian                          kurulum / devam / güncelleme / sağlık (global komut)
  guardian incele                   aşama sonu derin inceleme (global komut)
  guardian.py kontrol [--profil hizli|tam]   mekanik kontroller (varsayılan: tam)
  guardian.py hook                  Claude Code Stop hook adaptörü
  guardian.py doktor                bağlantı ve sağlık doğrulaması
  guardian.py --selftest

Çıkış kodları: 0 PASS, 1 BLOCK, 2 ERROR. Guardian'ın kendi çökmesi her zaman 2.

Aynı dosya iki rolde çalışır: depo kökünde (yanında kit/ varken) global `guardian`
komutudur; hedef projede .guardian/guardian.py olarak proje içi motordur. Proje kökü:
.guardian/ içindeyse onun üst klasörü, değilse çalışma dizini.

[ithalat] glob anlamı (yollar depoya göreli, '/' ayraçlı, büyük/küçük harf duyarlı):
  *  tek segment içinde her şey, '/' hariç     ?  tek karakter, '/' hariç
  ** yalnızca tam segment; sıfır veya daha fazla segment. 'a/**' hem 'a'yı hem altını tutar.
  '[]', '{}', '\\', boş/'.'/'..' segment, baştaki veya sondaki '/' yapılandırma hatasıdır.
"""
import sys

if sys.version_info < (3, 11):
    sys.stdout.write("ERROR G000 Python 3.11+ gerekli\n")
    sys.exit(2)

import difflib
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
from datetime import datetime
from functools import lru_cache
from pathlib import Path

VERSION = "2.0.1"
MARK = "<!-- guardian:v2 -->"
SELF = Path(__file__).resolve()
GUARDIAN_DIR = SELF.parent
IN_PROJECT = GUARDIAN_DIR.name == ".guardian"
PROJECT_ROOT = GUARDIAN_DIR.parent if IN_PROJECT else Path.cwd()
KIT = GUARDIAN_DIR / "kit"
TOML_REL = ".guardian/guardian.toml"
DURUM_REL = ".guardian/durum.json"
CACHE_REL = ".guardian/.cache"
MAX_LINES, MAX_TOTAL = 10, 30
HIZLI_SINIR = 20  # doktor: hizli profilin üst sınırı (saniye)
EPILOGUE = "Kullanıcı müdahalesi gerekli. .guardian/ dosyalarını değiştirme."
USAGE = __doc__.split("\n\n")[1]

# hedef yol -> kit kaynağı. Statik dosyalar her motor güncellemesinde yenilenir ve
# doktor özetlerini durum.json ile karşılaştırır; şablonlar yalnızca kurulumda yazılır.
STATIC = {
    ".guardian/guardian.py": None,  # bu dosyanın kendisi
    "CLAUDE.md": "CLAUDE.md",
    ".guardian/prompts/kurulum.md": "prompts/kurulum.md",
    ".guardian/prompts/guncelle.md": "prompts/guncelle.md",
    ".guardian/prompts/incele.md": "prompts/incele.md",
    ".guardian/git-hooks/pre-commit": "git-hooks/pre-commit",
    ".claude/commands/incele.md": "commands/incele.md",
}
TEMPLATES = {"AGENTS.md": "AGENTS.md", "DURUM.md": "DURUM.md", "KARARLAR.md": "KARARLAR.md",
             TOML_REL: "guardian.toml"}
CI_REL = ".github/workflows/guardian.yml"
SETTINGS_REL = ".claude/settings.json"
HOOKS_PATH = ".guardian/git-hooks"
HOOK_RX = re.compile(r"\.guardian/guardian\.py\"?\s+hook\b")
REQUIRED_ALLOW = ("Bash(python3 .guardian/guardian.py kontrol *)", "Bash(python3 .guardian/guardian.py doktor)",
                  "Bash(git add *)", "Bash(git commit *)")
REQUIRED_DENY = ("Edit(/.guardian/guardian.py)", "Edit(/.guardian/git-hooks/**)", "Edit(/.claude/settings.json)",
                 "Bash(git push --force *)", "Bash(git commit *--no-verify*)", "Bash(git commit -n*)",
                 "Bash(git commit * -n*)")
LIMITS = {"agents_md": 60, "claude_md": 80, "durum_md": 80, "karar_satir": 200}
KARAR_RX = re.compile(r"- K-\d{3,} · \d{4}-\d{2}-\d{2} · .+ — neden: .+")
PLACEHOLDER_RX = re.compile(r"\{\{[^}]*\}\}")


class GuardianError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def read(path, default=None):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- CONFIG

def _keys(d, req, opt, where):
    if not isinstance(d, dict):
        raise GuardianError("G002", f"{where}: tablo bekleniyor")
    miss, extra = req - d.keys(), d.keys() - req - opt
    if miss or extra:
        raise GuardianError("G002", f"{where}: " + "; ".join(
            x for x in (miss and f"eksik {sorted(miss)}", extra and f"bilinmeyen {sorted(extra)}") if x))


def _strs(v, where, allow_empty=False):
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v) or (not v and not allow_empty):
        raise GuardianError("G002", f"{where}: {'' if allow_empty else 'boş olmayan '}metin listesi bekleniyor")
    return v


def _str(v, where):
    if not isinstance(v, str) or not v.strip():
        raise GuardianError("G002", f"{where}: boş olmayan metin bekleniyor")
    return v


ECOS = {"composer": "composer.json", "npm": "package.json", "pub": "pubspec.yaml", "pip": "requirements.txt"}


class Config:
    def __init__(self, text):
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise GuardianError("G001", f"guardian.toml geçersiz: {e}")
        _keys(data, set(), {"ajanlar", "bagimliliklar", "limitler", "adim", "ithalat"}, "guardian.toml")
        aj = data.get("ajanlar", {})
        _keys(aj, set(), {"gelistirici"}, "[ajanlar]")
        self.gelistirici = _str(aj["gelistirici"], "[ajanlar].gelistirici") if "gelistirici" in aj else None
        dep = data.get("bagimliliklar", {})
        _keys(dep, set(), set(ECOS), "[bagimliliklar]")
        self.izinli = {e: _strs(dep.get(e, []), f"[bagimliliklar].{e}", True) for e in ECOS}
        lim = data.get("limitler", {})
        _keys(lim, set(), set(LIMITS), "[limitler]")
        for k, v in lim.items():
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise GuardianError("G002", f"[limitler].{k}: pozitif tam sayı bekleniyor")
        self.limit = {**LIMITS, **lim}
        self.adimlar, names = [], set()
        steps = data.get("adim", [])
        if not isinstance(steps, list):
            raise GuardianError("G002", "adim: [[adim]] bekleniyor")
        for a in steps:
            where = f"[[adim]] {a.get('ad', '?') if isinstance(a, dict) else a!r}"
            _keys(a, {"ad", "komut", "profil"}, {"zaman_asimi"}, where)
            if _str(a["ad"], where + ".ad") in names:
                raise GuardianError("G002", f"{where}: ad tekrarlanıyor")
            names.add(a["ad"])
            _str(a["komut"], where + ".komut")
            if a["profil"] not in ("hizli", "tam"):
                raise GuardianError("G002", f"{where}.profil: 'hizli' veya 'tam' olmalı")
            t = a.setdefault("zaman_asimi", 120)
            if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
                raise GuardianError("G002", f"{where}.zaman_asimi: pozitif tam sayı (saniye) bekleniyor")
            self.adimlar.append(a)
        it = data.get("ithalat", {"etkin": False})
        _keys(it, {"etkin"}, {"ignore", "resolve", "modules", "rules"}, "[ithalat]")
        if not isinstance(it["etkin"], bool):
            raise GuardianError("G002", "[ithalat].etkin: true/false bekleniyor")
        self.ithalat = ImportConfig(it) if it["etkin"] else None


def load_config(root):
    text = read(root / TOML_REL)
    if text is None:
        raise GuardianError("G001", f"{TOML_REL} okunamadı (kurulum eksik: `guardian` çalıştırın)")
    return Config(text), text


# ---------------------------------------------------------------- YERLEŞİK ADIM: bagimliliklar

PLATFORM_RX = re.compile(r"^(php|hhvm|composer(-plugin|-runtime)?-api|(ext|lib)-.+)$")


def packages(eco, path):
    text = read(path)
    if text is None:
        raise GuardianError("G015", f"{path.name} okunamadı")
    if eco in ("composer", "npm"):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise GuardianError("G015", f"{path.name} geçersiz JSON: {e}")
        keys = ("require", "require-dev") if eco == "composer" else (
            "dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
        out = []
        for k in keys:
            sec = data.get(k, {}) if isinstance(data, dict) else None
            if not isinstance(sec, dict):
                raise GuardianError("G015", f"{path.name}: '{k}' nesne olmalı")
            out += [p for p in sec if not (eco == "composer" and PLATFORM_RX.match(p))]
        return out
    if eco == "pub":
        out, sec, indent = [], False, None
        for line in text.splitlines():
            body = line.split("#", 1)[0].rstrip()
            if not body.strip():
                continue
            if not line[0].isspace():
                sec, indent = body.split(":")[0].strip() in ("dependencies", "dev_dependencies"), None
                continue
            ind = len(body) - len(body.lstrip())
            if sec and (indent is None or ind == indent):
                indent = ind
                out.append(body.strip().split(":")[0].strip())
        return out
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-") and (m := re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line)):
            out.append(re.sub(r"[-_.]+", "-", m.group()).lower())
    return out


def step_bagimliliklar(root, cfg):
    lines = []
    for eco, fname in ECOS.items():
        if (root / fname).is_file():
            for p in packages(eco, root / fname):
                if not any(fnmatch.fnmatchcase(p, pat) for pat in cfg.izinli[eco]):
                    lines.append(f"{fname}: '{p}' izin listesinde yok ([bagimliliklar].{eco})")
    return lines


# ---------------------------------------------------------------- YERLEŞİK ADIM: limitler

def step_limitler(root, cfg):
    lines, lim = [], cfg.limit
    texts = {}
    for f in ("AGENTS.md", "CLAUDE.md", "DURUM.md", "KARARLAR.md"):
        texts[f] = read(root / f)
        if texts[f] is None:
            raise GuardianError("G018", f"{f} yok veya okunamıyor (kurulum eksik: `guardian` çalıştırın)")
    n = sum(1 for x in texts["AGENTS.md"].splitlines() if x.strip())
    if n > lim["agents_md"]:
        lines.append(f"AGENTS.md: {n} satır (sınır {lim['agents_md']}, boş satırlar hariç). "
                     "Yeni kural için önce bir kuralı çıkar veya birleştir.")
    for f, k in (("CLAUDE.md", "claude_md"), ("DURUM.md", "durum_md")):
        n = len(texts[f].splitlines())
        if n > lim[k]:
            lines.append(f"{f}: {n} satır (sınır {lim[k]}). Kısalt; biten görevleri tek satırda topla.")
    for i, line in enumerate(texts["KARARLAR.md"].splitlines(), 1):
        if len(line) > lim["karar_satir"]:
            lines.append(f"KARARLAR.md:{i}: {len(line)} karakter (sınır {lim['karar_satir']})")
        elif line.strip() and not line.startswith(("#", "<!--")) and not KARAR_RX.fullmatch(line):
            lines.append(f"KARARLAR.md:{i}: biçim '- K-001 · YYYY-MM-DD · <karar> — neden: <gerekçe>' olmalı")
    return lines


# ---------------------------------------------------------------- PROJE ADIMLARI

def find_exe(root, cmd):
    """Komutun çalıştırılabilir dosyası (baştaki VAR=değer atamaları atlanır) bulunuyor mu?"""
    try:
        toks = [t for t in shlex.split(cmd) if not re.match(r"^\w+=", t)]
    except ValueError as e:
        raise GuardianError("G002", f"komut ayrıştırılamadı: {cmd!r}: {e}")
    if not toks:
        return None, False
    exe = toks[0]
    if "/" in exe:
        p = Path(exe) if exe.startswith("/") else root / exe
        return exe, p.is_file() and os.access(p, os.X_OK)
    return exe, shutil.which(exe) is not None


def step_komut(root, adim):
    exe, ok = find_exe(root, adim["komut"])
    if not ok:
        raise GuardianError("G013", f"adım '{adim['ad']}': '{exe}' bulunamadı. Aracı kurun veya "
                            "guardian.toml'daki komutu düzeltin.")
    try:
        p = subprocess.run(adim["komut"], shell=True, cwd=root, capture_output=True,
                           timeout=adim["zaman_asimi"])
    except subprocess.TimeoutExpired:
        raise GuardianError("G014", f"adım '{adim['ad']}' {adim['zaman_asimi']} sn'de bitmedi "
                            "(zaman_asimi'ni artırın veya adımı 'tam' profile alın)")
    if p.returncode == 0:
        return []
    if p.returncode in (126, 127):
        raise GuardianError("G013", f"adım '{adim['ad']}' çalıştırılamadı (exit {p.returncode}): "
                            + (p.stderr.decode(errors="replace").strip().splitlines() or [""])[-1])
    out = [x for x in (p.stdout + p.stderr).decode(errors="replace").splitlines() if x.strip()]
    return out or [f"exit {p.returncode}"]


# ---------------------------------------------------------------- KONTROL + ÖNBELLEK

def git(top, *args, raw=False):
    try:
        p = subprocess.run(["git", "-C", str(top), *args], capture_output=True)
    except OSError as e:
        raise GuardianError("G004", f"git kullanılamıyor: {e.strerror}")
    if p.returncode:
        raise GuardianError("G004", f"git {args[0]} başarısız: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout if raw else p.stdout.decode("utf-8", "replace")


def git_top(root):
    return Path(git(root, "rev-parse", "--show-toplevel").strip())


def tree_signature(root, profil, cfg_text):
    """İndeks + çalışma ağacı farkı + izlenmeyen dosyalar. Git yoksa None (önbellek yok)."""
    try:
        top = git_top(root)
        h = hashlib.sha256(f"{VERSION}\0{profil}\0{cfg_text}\0".encode())
        h.update(git(top, "ls-files", "-s", "-z", raw=True))
        h.update(git(top, "diff", "--binary", "--no-ext-diff", raw=True))
        for f in sorted(x for x in git(top, "ls-files", "-o", "--exclude-standard", "-z").split("\0") if x):
            h.update(f.encode() + b"\0")
            try:
                h.update((top / f).read_bytes())
            except OSError:
                h.update(b"\1")
        return h.hexdigest()
    except GuardianError:
        return None


def run_kontrol(root, profil):
    cfg, text = load_config(root)
    sig = tree_signature(root, profil, text)
    cache = root / CACHE_REL / f"kontrol-{profil}"
    if sig and read(cache) == sig:
        return 0, []
    steps = [("bagimliliklar", lambda: step_bagimliliklar(root, cfg)), ("limitler", lambda: step_limitler(root, cfg))]
    if cfg.ithalat:
        steps.append(("ithalat", lambda: step_ithalat(root, cfg.ithalat)))
    steps += [(a["ad"], lambda a=a: step_komut(root, a)) for a in cfg.adimlar
              if profil == "tam" or a["profil"] == "hizli"]
    blocks, errors = [], []
    for name, fn in steps:
        try:
            lines = fn()
        except GuardianError as e:
            errors.append(f"ERROR {e.code} {e}")
            continue
        if lines:
            blocks.append(f"BLOCK {name}")
            blocks += ["  " + x for x in lines[:MAX_LINES]]
            if len(lines) > MAX_LINES:
                blocks.append(f"  +{len(lines) - MAX_LINES} satır daha")
    if errors:
        return 2, errors[:MAX_LINES] + [EPILOGUE]
    if blocks:
        return 1, blocks[:MAX_TOTAL]
    if sig:
        write(cache, sig)
    return 0, []


def hizli_gecikti(sn, calisti):
    """hizli profil süresi sınırı aşıldıysa uyarı; önbellekten dönen çalıştırma ölçülmez."""
    if calisti and sn > HIZLI_SINIR:
        return (f"`kontrol --profil hizli` {sn:.1f} sn sürdü (sınır {HIZLI_SINIR}); "
                "yavaş adımları \"tam\" profile taşıyın")
    return None


# ---------------------------------------------------------------- HOOK

def read_durum(root):
    text = read(root / DURUM_REL)
    if text is None:
        raise GuardianError("G018", f"{DURUM_REL} yok: Guardian bu projede kurulu değil (`guardian` çalıştırın)")
    try:
        d = json.loads(text)
    except ValueError as e:
        raise GuardianError("G018", f"{DURUM_REL} geçersiz JSON: {e}")
    if not isinstance(d, dict) or not isinstance(d.get("asama"), str):
        raise GuardianError("G018", f"{DURUM_REL}: 'asama' alanı yok")
    return d


def write_durum(root, d):
    write(root / DURUM_REL, json.dumps(d, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def run_hook(root, stdin_text):
    """Stop hook: BLOCK -> exit 2 (ajan düzeltir); düzeltme denemesinden sonra hâlâ BLOCK ->
    ajan durur, kullanıcı görür; ERROR -> ajan hemen durur. Kurulum bitene kadar sessiz."""
    try:
        try:
            active = bool(json.loads(stdin_text or "{}").get("stop_hook_active"))
        except (ValueError, AttributeError):
            active = False
        try:
            if read_durum(root)["asama"] != "aktif" and not os.environ.get("GUARDIAN_DOKTOR"):
                return 0
            code, lines = run_kontrol(root, "hizli")
        except GuardianError as e:
            code, lines = 2, [f"ERROR {e.code} {e}", EPILOGUE]
        out = "\n".join(lines)
        if code == 0:
            return 0
        if code == 1 and not active:
            sys.stderr.write(out + "\nUygulama kodunu düzelt. Doğru çözüm yeni bağımlılık veya kural "
                             "değişikliği gerektiriyorsa etrafından dolanma; dur ve gerekçesini yaz.\n")
            return 2
        if code == 1:
            print(json.dumps({"systemMessage": "Guardian BLOCK çözülmedi:\n" + out}, ensure_ascii=False))
            return 0
    except BaseException as e:  # çökme asla ajan döngüsüne geri girmez
        out = f"ERROR G009 iç hata: {type(e).__name__}: {e}\n{EPILOGUE}"
    print(json.dumps({"continue": False, "stopReason": "Guardian ERROR\n" + out}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- TASARIM ENVANTERİ

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif"}
HTML_EXT = {".html", ".htm"}


def find_tasarim(root):
    c = [p.name for p in root.iterdir() if p.is_dir() and p.name.lower().replace("ı", "i") == "tasarim"]
    if len(c) > 1:
        raise GuardianError("G016", f"birden fazla tasarım klasörü var: {', '.join(sorted(c))}; birini bırakın")
    return c[0] if c else None


def envanter(root, tdir):
    """Her ekran tek satır: ad · HTML · görseller. Dosya içerikleri okunmaz."""
    lines = [MARK, "# Tasarım envanteri (guardian üretir; elle düzenlemeyin)", ""]
    if not tdir or not (root / tdir).is_dir():
        return "\n".join(lines + ["- (tasarım klasörü yok)"]) + "\n"
    base, groups = root / tdir, {}
    for p in sorted(base.rglob("*")):
        if p.is_file() and not any(s.startswith(".") for s in p.relative_to(base).parts) \
                and p.suffix.lower() in HTML_EXT | IMG_EXT:
            groups.setdefault(p.parent, []).append(p)
    screens = {}
    for d, fs in groups.items():
        rel = d.relative_to(base).as_posix()
        per_dir = rel != "." and sum(f.suffix.lower() in HTML_EXT for f in fs) <= 1
        for f in fs:
            name = rel if per_dir else (f.stem if rel == "." else f"{rel}/{f.stem}")
            html, imgs = screens.setdefault(name, ([], []))
            (html if f.suffix.lower() in HTML_EXT else imgs).append(f.relative_to(root).as_posix())
    for name, (html, imgs) in sorted(screens.items()):
        lines.append(f"- {name} · html: {', '.join(html) or '—'} · görsel: {', '.join(imgs) or '—'}")
    if not screens:
        lines.append("- (tasarım klasörü boş)")
    return "\n".join(lines) + "\n"


def screen_names(text):
    return {m.group(1) for m in re.finditer(r"(?m)^- (.+?) · html:", text or "")}


# ---------------------------------------------------------------- DOKTOR

def code_lines(text):
    """Kod bloğu dışındaki satırlar (Claude Code @ içe aktarması kod bloklarını atlar)."""
    out, fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence:
            out.append(line.strip())
    return out


def proje_file(root):
    c = [p for p in root.iterdir() if p.is_file() and p.name.lower() == "proje.md"]
    if len(c) > 1:
        raise GuardianError("G016", f"birden fazla proje dosyası var: {', '.join(p.name for p in c)}; birini bırakın")
    if not c or not read(c[0], "").strip():
        raise GuardianError("G016", "proje.md yok veya boş: proje kökünde projenin ayrıntılı tanımını "
                            "proje.md olarak yazın, sonra `guardian` çalıştırın.")
    return c[0]


def stop_commands(settings):
    out = []
    for group in (settings.get("hooks") or {}).get("Stop") or []:
        for h in (group.get("hooks") or []) if isinstance(group, dict) else []:
            if isinstance(h, dict) and h.get("type") == "command" and HOOK_RX.search(str(h.get("command", ""))):
                out.append(h["command"])
    return out


def help_problem(cmd, timeout=20):
    """Geliştirici komutundaki her bayrak, kurulu aracın --help çıktısında geçiyor mu?"""
    try:
        toks = shlex.split(cmd)
    except ValueError as e:
        return f"ayrıştırılamadı: {e}"
    if not toks or not shutil.which(toks[0]):
        return f"'{toks[0] if toks else cmd}' PATH'te yok"
    prefix = []
    for t in toks:
        if t.startswith("-"):
            break
        prefix.append(t)
    try:
        p = subprocess.run(prefix + ["--help"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"'{' '.join(prefix)} --help' çalışmadı: {e}"
    text = (p.stdout + p.stderr).decode(errors="replace")
    bad = [f for f in (t.split("=")[0] for t in toks if t.startswith("-"))
           if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", text)]
    return f"kurulu sürümde olmayan bayrak: {', '.join(bad)}" if bad else None


def kanarya_ithalat(icfg):
    for a, (dirs, deps) in icfg.modules.items():
        for b, (bdirs, _) in icfg.modules.items():
            if b != a and b not in deps:
                src = dirs[0] + "/guardian_kanarya.ts"
                rel = posixpath.relpath(bdirs[0] + "/x", dirs[0])
                with tempfile.TemporaryDirectory(prefix="guardian-kanarya-") as t:
                    write(Path(t) / src, f'import x from "{rel if rel.startswith(".") else "./" + rel}";\n')
                    return bool(evaluate(icfg, Path(t), [src]))
    return None


def run_doktor(root, skip=()):
    durum = read_durum(root)
    try:
        cfg, cfg_err = load_config(root)[0], None
    except GuardianError as e:
        cfg, cfg_err = None, f"guardian.toml: {e}"

    def need_cfg():
        if cfg is None:
            raise GuardianError("G001", cfg_err)
        return cfg

    def kit():
        out = []
        for rel in [*STATIC, *TEMPLATES, SETTINGS_REL, ".guardian/tasarim.md", ".guardian/proje.son.md"]:
            p = root / rel
            if not p.is_file():
                out.append(f"{rel} yok (geri getirin: git checkout -- {rel})")
            elif rel.endswith(".md") and rel != ".guardian/proje.son.md" and MARK not in read(p, ""):
                out.append(f"{rel}: Guardian işareti ({MARK}) yok")
            elif rel in (TOML_REL, ".guardian/git-hooks/pre-commit") and "guardian:v2" not in read(p, ""):
                out.append(f"{rel}: Guardian işareti (guardian:v2) yok")
            elif rel in STATIC and sha(p.read_bytes()) != (durum.get("statik") or {}).get(rel):
                out.append(f"{rel} kit sürümünden farklı (elle değişmiş; geri alın: git checkout -- {rel})")
        if durum.get("kit_surumu") != VERSION:
            out.append(f"sürüm uyuşmuyor: motor {VERSION}, durum.json {durum.get('kit_surumu')} (`guardian` çalıştırın)")
        return out

    def zincir():
        text = read(root / "CLAUDE.md")
        if text is None:
            return ["CLAUDE.md yok"]
        lines = code_lines(text)
        return [f"CLAUDE.md'de '@{f}' içe aktarması yok veya {f} yok" for f in ("AGENTS.md", "DURUM.md")
                if f"@{f}" not in lines or not (root / f).is_file()]

    def yer_tutucu():
        out = []
        for f in ("AGENTS.md", "DURUM.md", TOML_REL):
            for i, line in enumerate((read(root / f) or "").splitlines(), 1):
                if m := PLACEHOLDER_RX.search(line):
                    out.append(f"{f}:{i}: doldurulmamış yer tutucu {m.group()[:40]}")
        return out[:5]

    def settings_load():
        try:
            s = json.loads(read(root / SETTINGS_REL) or "")
        except ValueError:
            raise GuardianError("G018", f"{SETTINGS_REL} yok veya geçersiz JSON")
        if not isinstance(s, dict):
            raise GuardianError("G018", f"{SETTINGS_REL}: nesne bekleniyor")
        return s

    def claude_hook():
        s = settings_load()
        out = [] if stop_commands(s) else [f"{SETTINGS_REL}: Stop hook'u '.guardian/guardian.py hook' "
                                           "komutunu göstermiyor (bağlantı kopuk; `guardian` ile onarın)"]
        perms = s.get("permissions") or {}
        for kind, req in (("allow", REQUIRED_ALLOW), ("deny", REQUIRED_DENY)):
            miss = [r for r in req if r not in (perms.get(kind) or [])]
            if miss:
                out.append(f"{SETTINGS_REL}: permissions.{kind} eksik: {', '.join(miss)}")
        return out

    def hook_calisiyor():
        cmds = stop_commands(settings_load())
        if not cmds:
            return ["çalıştırılacak Stop hook'u yok"]
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), "GUARDIAN_DOKTOR": "1"}
        try:
            p = subprocess.run(cmds[0], shell=True, cwd=root, input=b"{}", capture_output=True, env=env, timeout=300)
        except subprocess.TimeoutExpired:
            return ["hook 300 sn'de yanıt vermedi"]
        so, se = p.stdout.decode(errors="replace").strip(), p.stderr.decode(errors="replace").strip()
        if p.returncode == 2 and se:
            return [f"hook BLOCK döndü (kontrol geçmiyor): {se.splitlines()[0]}"]
        if p.returncode == 0 and not so:
            return []
        try:
            j = json.loads(so) if p.returncode == 0 else None
        except ValueError:
            j = None
        if not isinstance(j, dict):
            return [f"hook protokol dışı yanıt verdi (exit {p.returncode}): {(so or se)[:120]}"]
        if j.get("continue") is False:
            return ["hook ERROR döndü: " + " ".join(str(j.get("stopReason", "")).split())[:160]]
        return []

    def git_hook():
        try:
            hp = git(root, "config", "--get", "core.hooksPath").strip()
        except GuardianError:
            hp = ""
        out = [] if hp == HOOKS_PATH else [f"git core.hooksPath = '{hp}' (olmalı: {HOOKS_PATH}; "
                                           f"onarım: git config core.hooksPath {HOOKS_PATH})"]
        pc = root / HOOKS_PATH / "pre-commit"
        if not pc.is_file() or not os.access(pc, os.X_OK):
            out.append(f"{HOOKS_PATH}/pre-commit yok veya çalıştırılabilir değil (chmod +x)")
        elif "guardian.py kontrol" not in read(pc, ""):
            out.append(f"{HOOKS_PATH}/pre-commit guardian.py kontrol çağırmıyor")
        return out

    def kanarya():
        c, out = need_cfg(), []
        with tempfile.TemporaryDirectory(prefix="guardian-kanarya-") as t:
            t = Path(t)
            write(t / "composer.json", '{"require": {"guardian-kanarya/paket": "*"}}')
            write(t / "package.json", '{"dependencies": {"guardian-kanarya-paket": "*"}}')
            write(t / "pubspec.yaml", "dependencies:\n  guardian_kanarya_paket: any\n")
            write(t / "requirements.txt", "guardian-kanarya-paket==1.0\n")
            if len(step_bagimliliklar(t, c)) != len(ECOS):
                out.append("bagimliliklar kanaryası BLOCK almadı (izin listesi her şeyi geçiriyor; '*' desenini kaldırın)")
        if c.ithalat:
            r = kanarya_ithalat(c.ithalat)
            if not r:
                out.append("ithalat kanaryası " + ("kurulamadı: birbirine bağımlı olmayan en az iki modül gerekli"
                                                   if r is None else "BLOCK almadı (ignore/except çok geniş)"))
        return out

    def adimlar():
        c = need_cfg()
        out = [f"adım '{a['ad']}': '{exe}' bulunamadı" for a in c.adimlar
               for exe, ok in [find_exe(root, a["komut"])] if not ok]
        if out:
            return out
        cache = root / CACHE_REL / "kontrol-hizli"
        once = read(cache)  # önbellek hit'i dosyayı dokunulmaz bırakır; değişim = kontrol çalıştı
        t0 = time.monotonic()
        code, lines = run_kontrol(root, "hizli")
        sn = time.monotonic() - t0
        if code:
            return [f"`kontrol --profil hizli` exit {code}: " + " | ".join(
                x.strip() for x in lines[:3])]
        if gecikme := hizli_gecikti(sn, read(cache) != once):
            out.append(gecikme)
        return out

    def ajanlar():
        out = [f"'{x}' CLI'ı PATH'te yok" for x in ("claude", "codex") if not shutil.which(x)]
        c = need_cfg()
        if not c.gelistirici:
            out.append("[ajanlar].gelistirici tanımlı değil")
        elif problem := help_problem(c.gelistirici):
            out.append(f"[ajanlar].gelistirici geçersiz: {problem}")
        return out

    def senkron():
        out = []
        try:
            pf = proje_file(root)
            if sha(pf.read_bytes()) != durum.get("proje_hash"):
                out.append(f"{pf.name} değişti; `guardian` çalıştırın")
        except GuardianError as e:
            out.append(str(e))
        if read(root / ".guardian/tasarim.md") != envanter(root, find_tasarim(root)):
            out.append("tasarim.md güncel değil; `guardian` çalıştırın")
        return out

    def limitler():
        return step_limitler(root, need_cfg())

    def ci():
        if not durum.get("ci"):
            return []
        text = read(root / CI_REL)
        if text is None:
            return [f"{CI_REL} yok"]
        return [] if "guardian.py kontrol --profil tam" in text else [f"{CI_REL} 'kontrol --profil tam' çağırmıyor"]

    def gitignore():
        lines = [x.strip() for x in (read(root / ".gitignore") or "").splitlines()]
        return [] if CACHE_REL + "/" in lines else [f".gitignore '{CACHE_REL}/' içermiyor"]

    checks = [("kit", kit), ("zincir", zincir), ("yer-tutucu", yer_tutucu), ("claude-hook", claude_hook),
              ("hook-calisiyor", hook_calisiyor), ("git-hook", git_hook), ("kanarya", kanarya),
              ("adimlar", adimlar), ("ajanlar", ajanlar), ("senkron", senkron), ("limitler", limitler),
              ("ci", ci), ("gitignore", gitignore)]
    fails, failed = [], 0
    for name, fn in checks:
        if name in skip:
            continue
        try:
            msgs = fn()
        except GuardianError as e:
            msgs = [str(e)]
        failed += bool(msgs)
        fails += [f"✗ {name}: {m}" for m in msgs[:MAX_LINES]]
    total = len(checks) - len(skip)
    tail = f"doktor: {total - failed}/{total} geçti"
    if not fails and not skip and durum["asama"] != "aktif":
        durum["asama"] = "aktif"
        write_durum(root, durum)
        tail += " · kurulum tamamlandı (asama = aktif)"
    return (1 if fails else 0), fails + [tail]


# ---------------------------------------------------------------- GLOBAL `guardian`

def say(*lines):
    print("\n".join(lines), flush=True)


def onay(soru):
    try:
        return input(f"{soru} [e/H] ").strip().lower() in ("e", "evet", "y", "yes")
    except EOFError:
        return False


def claude_cmd():
    env = os.environ.get("GUARDIAN_CLAUDE_CMD")
    cmd = shlex.split(env) if env else ["claude"]
    if not cmd or not shutil.which(cmd[0]):
        raise GuardianError("G017", f"'{cmd[0] if cmd else env}' bulunamadı: Claude Code CLI'ı kurun "
                            "(https://code.claude.com) ve tekrar `guardian` yazın.")
    if not env:
        p = subprocess.run(cmd + ["--help"], capture_output=True, timeout=30)
        if "[prompt]" not in p.stdout.decode(errors="replace"):
            raise GuardianError("G017", "kurulu claude sürümü başlangıç mesajı almıyor ('claude [prompt]'); güncelleyin")
    return cmd


def claude_ac(root, prompt):
    say("", "Claude Code açılıyor…", "")
    return subprocess.run(claude_cmd() + [prompt], cwd=root).returncode


def kit_text(src):
    return SELF.read_bytes() if src is None else (KIT / src).read_bytes()


def install_static(root, durum):
    changed, hashes = [], {}
    for rel, src in STATIC.items():
        data, p = kit_text(src), root / rel
        if not p.is_file() or p.read_bytes() != data:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            changed.append(rel)
        hashes[rel] = sha(data)
    for rel in (".guardian/guardian.py", ".guardian/git-hooks/pre-commit"):
        (root / rel).chmod(0o755)
    durum.update(kit_surumu=VERSION, statik=hashes)
    return changed


def merge_settings(cur, kit):
    hooks = cur.setdefault("hooks", {})
    perms = cur.setdefault("permissions", {})
    if not isinstance(hooks, dict) or not isinstance(perms, dict) or not isinstance(hooks.setdefault("Stop", []), list):
        raise GuardianError("G018", f"{SETTINGS_REL}: 'hooks'/'permissions' beklenen yapıda değil; elle düzeltin")
    if not stop_commands(cur):
        hooks["Stop"] += kit["hooks"]["Stop"]
    for kind in ("allow", "deny"):
        lst = perms.setdefault(kind, [])
        lst += [r for r in kit["permissions"][kind] if r not in lst]
    return cur


def settings_birlestir(root):
    """Kit settings'ini projeye birleştirir (yalnızca eksikleri ekler); dosya değiştiyse True."""
    kit_settings = json.loads((KIT / "claude-settings.json").read_text(encoding="utf-8"))
    sp = root / SETTINGS_REL
    try:
        cur = json.loads(read(sp) or "{}")
    except ValueError:
        raise GuardianError("G018", f"{SETTINGS_REL} geçersiz JSON; düzeltin veya silin")
    new = json.dumps(merge_settings(cur, kit_settings), indent=2, ensure_ascii=False) + "\n"
    if new == read(sp, ""):
        return False
    write(sp, new)
    return True


def kurulum(root):
    say("Guardian V2 kurulumu", "")
    for x in ("git",):
        if not shutil.which(x):
            raise GuardianError("G017", "git bulunamadı: kurun ve tekrar `guardian` yazın.")
    claude_cmd()
    if not shutil.which("codex"):
        say("Uyarı: 'codex' CLI'ı PATH'te yok. Geliştirici ajan olmadan Claude kodu yazamaz; doktor bunu eksik sayar.")
        if not onay("Yine de devam edilsin mi?"):
            raise GuardianError("G017", "kurulum iptal edildi (codex yok)")
    try:
        top = git_top(root)
    except GuardianError:
        if not onay("Bu klasör git reposu değil. `git init` yapılsın mı?"):
            raise GuardianError("G016", "Guardian git reposu gerektirir; kurulum iptal edildi")
        git(root, "init", "-q")
        top = git_top(root)
    if top.resolve() != root.resolve():
        raise GuardianError("G016", f"`guardian`ı git kökünde çalıştırın: {top}")
    pf = proje_file(root)
    tdir = find_tasarim(root)
    if tdir is None and not onay("Tasarım klasörü (TASARIM/) bulunamadı. Tasarımsız devam edilsin mi?"):
        raise GuardianError("G016", "kurulum iptal edildi: TASARIM/ klasörünü ekleyip tekrar `guardian` yazın")

    stamp, backups = datetime.now().strftime("%Y%m%d-%H%M%S"), []
    durum = {"asama": "claude-bekliyor"}
    targets = {**TEMPLATES, **{r: s for r, s in STATIC.items() if r.endswith(".md")}}
    for rel in [*targets, SETTINGS_REL]:
        p = root / rel
        ours = HOOK_RX.search(read(p, "")) if rel == SETTINGS_REL else MARK in read(p, "")
        if p.is_file() and not ours:
            b = root / ".guardian/yedek" / stamp / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, b)
            backups.append(rel)
    for rel, src in TEMPLATES.items():
        p = root / rel
        if not p.is_file() or rel in backups:
            write(p, (KIT / src).read_text(encoding="utf-8"))
    install_static(root, durum)
    settings_birlestir(root)
    try:
        durum["ci"] = "github.com" in git(root, "remote", "get-url", "origin")
    except GuardianError:
        durum["ci"] = False
    if durum["ci"] and not (root / CI_REL).is_file():
        write(root / CI_REL, (KIT / "ci/guardian.yml").read_text(encoding="utf-8"))
    git(root, "config", "core.hooksPath", HOOKS_PATH)
    gi = read(root / ".gitignore", "")
    if CACHE_REL + "/" not in [x.strip() for x in gi.splitlines()]:
        write(root / ".gitignore", gi + ("" if not gi or gi.endswith("\n") else "\n") + CACHE_REL + "/\n")
    write(root / ".guardian/tasarim.md", envanter(root, tdir))
    shutil.copyfile(pf, root / ".guardian/proje.son.md")
    durum["proje_hash"] = sha(pf.read_bytes())
    write_durum(root, durum)
    say(f"✓ kit kopyalandı (v{VERSION}); git hook'u ve Claude Stop hook'u bağlandı",
        f"✓ proje dosyası: {pf.name} · tasarım: {tdir or 'yok'} · CI: {'GitHub' if durum['ci'] else 'yok'}")
    if backups:
        say(f"✓ mevcut dosyalar yedeklendi: .guardian/yedek/{stamp}/ ({', '.join(backups)})")
    say('Claude Code bu klasöre güvenmek isteyip istemediğinizi sorarsa "evet" deyin; yoksa izinler yüklenmez.')
    return claude_kurulum(root)


def claude_kurulum(root):
    claude_ac(root, read(root / ".guardian/prompts/kurulum.md"))
    code, lines = run_doktor(root)
    say("", *lines)
    if read_durum(root)["asama"] != "aktif":
        say("Kurulum tamamlanmadı; `guardian` yazarak devam edin.")
        return code or 1
    return 0


def motor_guncelle(root, durum):
    old = durum.get("kit_surumu")
    changed = install_static(root, durum)
    write_durum(root, durum)
    paths = changed + [DURUM_REL] + ([SETTINGS_REL] if settings_birlestir(root) else [])
    say(f"Motor güncellendi: {old} → {VERSION} ({len(changed)} statik dosya)")
    env = {**os.environ, "GUARDIAN_GUNCELLEME": "1"}
    git(root, "add", "--", *paths)
    p = subprocess.run(["git", "-C", str(root), "commit", "-qm", f"guardian: motor {VERSION}", "--", *paths],
                       env=env, capture_output=True)
    if p.returncode:
        say("Motor güncellemesi commit edilemedi. Elle: "
            f"GUARDIAN_GUNCELLEME=1 git commit -m 'guardian: motor {VERSION}' -- {' '.join(paths)}",
            *p.stdout.decode(errors="replace").strip().splitlines()[:5])


def proje_guncelle(root, durum, pf):
    old = read(root / ".guardian/proje.son.md", "")
    diff = "".join(difflib.unified_diff(old.splitlines(True), pf.read_text(encoding="utf-8").splitlines(True),
                                        "proje.son.md", pf.name))
    say(f"{pf.name} değişti; güncelleme modu.")
    claude_ac(root, read(root / ".guardian/prompts/guncelle.md") + f"\n\n## Diff\n\n```diff\n{diff}```\n")
    code, lines = run_doktor(root, skip=("senkron",))
    if code:
        say(*lines, "Güncelleme tamamlanmadı; `guardian` yazarak tekrar deneyin.")
        return
    shutil.copyfile(pf, root / ".guardian/proje.son.md")
    durum = read_durum(root)
    durum["proje_hash"] = sha(pf.read_bytes())
    write_durum(root, durum)


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0")))


def cmd_guardian(root):
    durum = read_durum(root) if (root / DURUM_REL).exists() else None
    if durum is None:
        return kurulum(root)
    if durum["asama"] != "aktif":
        say("Yarım kalan kurulum bulundu; kaldığı yerden devam ediliyor.")
        return claude_kurulum(root)
    if vtuple(durum.get("kit_surumu")) < vtuple(VERSION):
        motor_guncelle(root, durum)
    elif vtuple(durum.get("kit_surumu")) > vtuple(VERSION):
        say(f"Uyarı: proje daha yeni bir Guardian ({durum.get('kit_surumu')}) ile kurulmuş; global guardian {VERSION}.")
    pf = proje_file(root)
    if sha(pf.read_bytes()) != durum.get("proje_hash"):
        proje_guncelle(root, durum, pf)
    tp, new = root / ".guardian/tasarim.md", envanter(root, find_tasarim(root))
    old = read(tp)
    if old != new:
        write(tp, new)
        a, b = screen_names(old), screen_names(new)
        say("Tasarım envanteri yenilendi." + "".join(
            f" {k}: {', '.join(sorted(v))}." for k, v in (("Yeni ekran", b - a), ("Silinen ekran", a - b)) if v))
    code, lines = run_doktor(root)
    say(*lines)
    return code


def cmd_incele(root):
    if read_durum(root)["asama"] != "aktif":
        raise GuardianError("G018", "kurulum tamamlanmamış; önce `guardian` çalıştırın")
    claude_ac(root, read(root / ".guardian/prompts/incele.md"))
    return 0


# ---------------------------------------------------------------- [ithalat]: isteğe bağlı import sınırı motoru

@lru_cache(maxsize=None)
def glob_rx(pat):
    bad = GuardianError("G002", f"desteklenmeyen glob {pat!r}")
    if not isinstance(pat, str) or not pat or pat[0] == "/" or pat[-1] == "/" \
            or any(c in pat for c in "[]{}\\"):
        raise bad
    segs = []
    for s in pat.split("/"):
        if s in ("", ".", "..") or ("**" in s and s != "**"):
            raise bad
        if not (s == "**" and segs and segs[-1] == "**"):
            segs.append(s)
    rx = ""
    for i, s in enumerate(segs):
        last = i == len(segs) - 1
        if s == "**":
            if not last:
                rx += "(?:[^/]+/)*"
            elif rx:
                rx = rx[:-1] + "(?:/.*)?"
            else:
                rx = ".*"
        else:
            rx += "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in s)
            rx += "" if last else "/"
    return re.compile(rx, re.S)


def gmatch(pat, path):
    return glob_rx(pat).fullmatch(path) is not None


LANGS = ("js", "py", "php", "go")
RULE_KEYS = {
    "forbid_import": ({"id", "type", "from", "targets", "msg"}, {"except"}),
    "module_deps": ({"id", "type"}, {"msg", "except"}),
}


class ImportConfig:
    def __init__(self, data):
        self.ignore = _strs(data.get("ignore", []), "[ithalat].ignore", True)
        for g in self.ignore:
            glob_rx(g)
        res = data.get("resolve", {})
        _keys(res, set(), set(LANGS), "[ithalat.resolve]")
        self.resolve = {}
        for lang in LANGS:
            table = res.get(lang, {"": ""} if lang == "py" else {})
            if not isinstance(table, dict) or not all(isinstance(v, str) for v in table.values()):
                raise GuardianError("G002", f"[ithalat.resolve].{lang}: metin tablosu bekleniyor")
            self.resolve[lang] = sorted(((k.replace("\\", "/"), v) for k, v in table.items()),
                                        key=lambda kv: -len(kv[0]))
        mods = data.get("modules", {})
        _keys(mods, set(), set(mods) if isinstance(mods, dict) else set(), "[ithalat.modules]")
        self.modules, self.dirs = {}, []  # ad -> (klasörler, depends); [(klasör, ad)]
        for name, m in mods.items():
            where = f"[ithalat.modules.{name}]"
            _keys(m, {"paths", "depends"}, set(), where)
            dirs = []
            for p in _strs(m["paths"], where + ".paths"):
                glob_rx(p)
                d = p[:-3]
                if not p.endswith("/**") or not d or any(c in d for c in "*?"):
                    raise GuardianError("G002", f"{where}: yol {p!r} '<sabit klasör>/**' olmalı")
                dirs.append(d)
            self.modules[name] = (dirs, _strs(m["depends"], where + ".depends", True))
            self.dirs += [(d, name) for d in dirs]
        for name, (_, deps) in self.modules.items():
            for dep in deps:
                if dep not in self.modules or dep == name:
                    raise GuardianError("G002", f"[ithalat.modules.{name}].depends: geçersiz modül {dep!r}")
        for i, (a, na) in enumerate(self.dirs):
            for b, nb in self.dirs[i + 1:]:
                if na != nb and (a == b or a.startswith(b + "/") or b.startswith(a + "/")):
                    raise GuardianError("G003", f"modül yolları çakışıyor: {na} ({a}) / {nb} ({b})")
        self.forbid, self.deps, ids = [], None, set()
        rules = data.get("rules", [])
        if not isinstance(rules, list):
            raise GuardianError("G002", "[ithalat].rules: [[ithalat.rules]] bekleniyor")
        for r in rules:
            if not isinstance(r, dict) or r.get("type") not in RULE_KEYS:
                raise GuardianError("G002", f"kural {r.get('id') if isinstance(r, dict) else r!r}: bilinmeyen type")
            where = f"kural {r.get('id', '?')}"
            _keys(r, *RULE_KEYS[r["type"]], where)
            if _str(r["id"], where + ".id") in ids:
                raise GuardianError("G002", f"{where}: id tekrarlanıyor")
            ids.add(r["id"])
            r["except"] = self._exceptions(r.get("except", []), where)
            if r["type"] == "forbid_import":
                _str(r["msg"], where + ".msg")
                for g in _strs(r["from"], where + ".from") + _strs(r["targets"], where + ".targets"):
                    glob_rx(g)
                self.forbid.append(r)
            elif self.deps:
                raise GuardianError("G002", f"{where}: yalnızca bir module_deps kuralı olabilir")
            else:
                _str(r.setdefault("msg", "depends dışında modül bağımlılığı"), where + ".msg")
                self.deps = r
        if self.deps is None:
            self.deps = {"id": "DEPS", "msg": "depends dışında modül bağımlılığı", "except": []}

    def _exceptions(self, ex, where):
        if not isinstance(ex, list):
            raise GuardianError("G002", f"{where}.except: liste bekleniyor")
        out = []
        for e in ex:
            _keys(e, {"path", "reason"}, set(), where + ".except")
            _str(e["reason"], where + ".except.reason")
            glob_rx(_str(e["path"], where + ".except.path"))
            for d, _ in self.dirs:
                if gmatch(e["path"], d) or gmatch(e["path"], d + "/_guardian_probe_/_probe_"):
                    raise GuardianError("G002", f"{where}: istisna {e['path']!r} çok geniş ({d} kapsanıyor)")
            out.append(e["path"])
        return out

    def module_of(self, path):
        for d, name in self.dirs:
            if path == d or path.startswith(d + "/"):
                return name
        return None


EXT_LANG = {e: "js" for e in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")}
EXT_LANG.update({".py": "py", ".pyi": "py", ".php": "php", ".go": "go"})
NON_CODE_EXT = set(
    ".md .mdx .txt .rst .json .jsonc .yaml .yml .toml .ini .cfg .lock .css .scss .sass .less "
    ".html .htm .svg .png .jpg .jpeg .gif .webp .avif .ico .bmp .woff .woff2 .ttf .otf .eot "
    ".map .csv .tsv .xml .pdf .mp3 .mp4 .webm .wav .snap .log .sql .graphql .gql .env "
    ".example .sample .gz .zip".split())
NON_CODE_NAMES = {".gitkeep", ".keep", ".gitignore", ".gitattributes", ".DS_Store",
                  ".editorconfig", "README", "LICENSE", "CODEOWNERS", "py.typed"}


def classify(path):
    """Dil anahtarı; bilinen kod dışı dosya için '', desteklenmeyen/bilinmeyen için None."""
    base = posixpath.basename(path)
    ext = posixpath.splitext(base)[1].lower()
    if ext in EXT_LANG:
        return EXT_LANG[ext]
    return "" if ext in NON_CODE_EXT or base in NON_CODE_NAMES else None


_C = r"(?P<c>//[^\n]*|/\*.*?\*/)"
_DQ, _SQ = r'"(?:\\.|[^"\\\n])*"', r"'(?:\\.|[^'\\\n])*'"
TOKENS = {
    "js": re.compile(_C + rf"|(?P<s>{_DQ}|{_SQ})|(?P<t>`(?:\\.|[^`\\])*`)"
                     r"|(?P<r>/(?![/*])(?:\\.|\[(?:\\.|[^\]\\\n])*\]|[^/\\\n\[])+/)", re.S),
    "go": re.compile(_C + rf"|(?P<s>{_DQ}|`[^`]*`)|(?P<x>{_SQ})", re.S),
    "py": re.compile(r'(?P<c>#[^\n]*)|(?P<s>"""(?:\\.|[^\\])*?"""|' + r"'''(?:\\.|[^\\])*?'''|"
                     + f"{_DQ}|{_SQ})", re.S),
    "php": re.compile(r"(?P<c>//[^\n]*|#(?!\[)[^\n]*|/\*.*?\*/)"
                      r"""|(?P<x>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'"""
                      r"""|<<<[ \t]*(?P<q>['"]?)(?P<hid>\w+)(?P=q)[^\n]*\n.*?\n[ \t]*(?P=hid)\b)""", re.S),
}
_KINDS = ("c", "s", "t", "r", "x")
_REGEX_PREV = set("(,=:[!&|?{};+-*%>~^")  # "<" yok: JSX "</div>"
_REGEX_KW = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void",
             "throw", "instanceof", "yield", "await"}


def _blank(s):
    return "\n".join(" " * len(p) for p in s.split("\n"))


def scan(text, lang):
    """Yorumları ve sabit olmayan metinleri boşaltır (konumlar korunur). Sabit metinler
    \\x01...\\x01 yer tutucusu olur; strings[konum] ham içeriği tutar."""
    rx, out, strings, pos = TOKENS[lang], [], {}, 0
    while m := rx.search(text, pos):
        kind = next(k for k in _KINDS if k in rx.groupindex and m.group(k) is not None)
        s, e, tok = m.start(), m.end(), m.group()
        if kind == "r":  # JS: regex mi bölme mi?
            j = s - 1
            while j >= 0 and text[j] in " \t\r\n":
                j -= 1
            if j >= 0 and text[j] not in _REGEX_PREV:
                w = re.search(r"[\w$]+$", text[max(0, j - 12):j + 1])
                if not w or w.group() not in _REGEX_KW:
                    out.append(text[pos:s + 1])
                    pos = s + 1
                    continue
        out.append(text[pos:s])
        if kind == "s" or (kind == "t" and "${" not in tok):
            out.append("\x01" + _blank(tok[1:-1]) + "\x01")
            strings[s] = tok[1:-1]
        else:
            out.append(_blank(tok))
        pos = e
    out.append(text[pos:])
    return "".join(out), strings


def _norm(p):
    p = posixpath.normpath(p) if p else "."
    if p == ".":
        return ""
    return None if p == ".." or p.startswith(("../", "/")) else p


def _by_prefix(s, table):
    for k, v in table:
        if s.startswith(k):
            return _norm(v + s[len(k):])
    return None


_S = r"(\x01[^\x01]*\x01)"
_SRX = re.compile(_S)
JS_RX = [re.compile(r"(?<![\w$.])" + p + _S) for p in (
    r"import\s+[\w$\s{},*]*?\bfrom\s*",
    r"export\s+[\w$\s{},*]*?\bfrom\s*",
    r"import\s*",
    r"import\s*\(\s*",
    r"require\s*\(\s*",
)]


def extract_js(code, strings, path, table):
    out = []
    for rx in JS_RX:
        for m in rx.finditer(code):
            spec = strings[m.start(1)]
            if spec.startswith(("./", "../")) or spec in (".", ".."):
                out.append((m.start(1), _norm(posixpath.join(posixpath.dirname(path), spec))))
            else:
                out.append((m.start(1), _by_prefix(spec, table)))
    return out


PY_FROM = re.compile(r"(?m)^[ \t]*from[ \t]+(\.*)([\w.]*)[ \t]+import[ \t]*(\([^)]*\)|[^\n;]*)")
PY_IMPORT = re.compile(r"(?m)^[ \t]*import[ \t]+([^\n;]+)")


def extract_py(code, strings, path, table):
    out = []
    for m in PY_FROM.finditer(code):
        dots, mod, names = m.groups()
        if dots:
            parts = [p for p in posixpath.dirname(path).split("/") if p]
            up = len(dots) - 1
            if up > len(parts):
                continue
            tgt = _norm(posixpath.join("/".join(parts[:len(parts) - up]), mod.replace(".", "/")))
        else:
            tgt = _by_prefix(mod.replace(".", "/"), table)
        if tgt is None:
            continue
        out.append((m.start(2), tgt))
        for n in names.replace("\\", " ").strip("() \t\n").split(","):
            n = n.split()[0] if n.split() else ""
            if n and n != "*":
                out.append((m.start(2), _norm(posixpath.join(tgt, n))))
    for m in PY_IMPORT.finditer(code):
        for n in m.group(1).replace("\\", " ").split(","):
            if n.split():
                out.append((m.start(1), _by_prefix(n.split()[0].replace(".", "/"), table)))
    return out


PHP_TOK = re.compile(r"(?<![\w$])(\\?[A-Za-z_]\w*(?:\\[A-Za-z_]\w*)+|\\[A-Za-z_]\w*)"
                     r"|(?<![\w$\\>:])(?:(namespace)\b|(use)\b)|([{}])")
PHP_NS = re.compile(r"\s+([A-Za-z_][\w\\]*)\s*([;{])|\s*(\{)")
PHP_USE = re.compile(r"\s+(?!\()([^;{]*(?:\{[^}]*\}[^;{]*)?);")
PHP_TRAIT = re.compile(r"\s+([\\\w\s,]+?)\s*[;{]")


def _php_use(body):
    """Üst düzey use ifadesinin (fqcn, takma ad) çiftleri."""
    body = re.sub(r"^(?:function|const)\s+", "", " ".join(body.split()))
    if "{" in body:
        prefix, _, inner = body.partition("{")
        prefix = prefix.strip().rstrip("\\")
        items = [prefix + "\\" + re.sub(r"^(?:function|const)\s+", "", i.strip())
                 for i in inner.rstrip("} ").split(",") if i.strip()]
    else:
        items = [i.strip() for i in body.split(",") if i.strip()]
    for it in items:
        name, _, alias = it.partition(" as ")
        name = name.strip().lstrip("\\")
        yield name, alias.strip() or name.rsplit("\\", 1)[-1]


def extract_php(code, strings, path, table):
    out, aliases, ns = [], {}, ""
    depth = imp_depth = skip = 0

    def emit(pos, fq):
        out.append((pos, _by_prefix(fq.lstrip("\\").replace("\\", "/"), table)))

    def resolve(name):
        if name.startswith("\\"):
            return name[1:]
        head, _, rest = name.partition("\\")
        if head.lower() == "namespace":
            return f"{ns}\\{rest}" if ns else rest
        if head in aliases:
            return aliases[head] + (f"\\{rest}" if rest else "")
        return f"{ns}\\{name}" if ns else name

    for m in PHP_TOK.finditer(code):
        if m.start() < skip:
            continue
        name, kw_ns, kw_use, brace = m.groups()
        if kw_ns:
            d = PHP_NS.match(code, m.end())
            if d and d.group(1):
                ns, aliases, skip = d.group(1), {}, d.end(1)
                imp_depth = depth + (d.group(2) == "{")
            elif d:
                ns, aliases, imp_depth = "", {}, depth + 1
        elif kw_use:
            if depth > imp_depth:  # trait use (closure `use (` eşleşmez)
                t = PHP_TRAIT.match(code, m.end())
                if t:
                    for n in t.group(1).split(","):
                        if n.strip():
                            emit(m.start(), resolve(n.strip()))
                    skip = t.end(1)
            elif u := PHP_USE.match(code, m.end()):
                for fq, alias in _php_use(u.group(1)):
                    aliases[alias] = fq
                    emit(m.start(), fq)
                skip = u.end()
        elif brace:
            depth += 1 if brace == "{" else -1
        else:
            emit(m.start(), resolve(name))
    return out


GO_GROUP = re.compile(r"(?<![\w.])import\s*\(([^)]*)\)")
GO_ONE = re.compile(r"(?<![\w.])import\s+(?:[\w.]+\s+)?" + _S)


def extract_go(code, strings, path, table):
    hits = [m.start(1) for m in GO_ONE.finditer(code)]
    for g in GO_GROUP.finditer(code):
        hits += [m.start() for m in _SRX.finditer(code, g.start(1), g.end(1))]
    return [(p, _by_prefix(strings[p], table)) for p in hits]


EXTRACTORS = {"js": extract_js, "py": extract_py, "php": extract_php, "go": extract_go}


def extract(lang, path, text, icfg):
    """[(satır, hedef_yol)]: iç statik importlar; harici importlar atılır."""
    code, strings = scan(text, lang)
    seen, out = set(), []
    for pos, tgt in EXTRACTORS[lang](code, strings, path, icfg.resolve[lang]):
        if not tgt:
            continue
        stem, ext = posixpath.splitext(tgt)
        if ext.lower() in EXT_LANG:
            tgt = stem
        key = (code.count("\n", 0, pos) + 1, tgt)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def evaluate(icfg, root, files):
    """BLOCK satırları; kontrol edilemeyen kapsam içi dosya ERROR'dır (fail-closed)."""
    out = []

    def hit(rule, f, text):
        if not any(gmatch(p, f) for p in rule["except"]):
            out.append(f"{rule['id']} {text}")

    for f in sorted(set(files)):
        full = root / f
        if not full.is_file() or any(gmatch(g, f) for g in icfg.ignore):
            continue
        mod = icfg.module_of(f)
        forbid = [r for r in icfg.forbid if any(gmatch(g, f) for g in r["from"])]
        lang = classify(f)
        if mod is None and not forbid or lang == "":
            continue
        if lang is None:
            raise GuardianError("G006", f"{f}: bu dosya türü için import çıkarıcı yok (kural kapsamında)")
        if lang in ("php", "go") and not icfg.resolve[lang]:
            raise GuardianError("G007", f"{f}: [ithalat.resolve].{lang} gerekli")
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise GuardianError("G008", f"{f}: {e.strerror}")
        for line, tgt in extract(lang, f, text, icfg):
            loc = f"{f}:{line} -> {tgt}"
            for r in forbid:
                if any(gmatch(t, tgt) for t in r["targets"]):
                    hit(r, f, f"{loc} {r['msg']}")
            n = icfg.module_of(tgt) if mod else None
            if n and n != mod and n not in icfg.modules[mod][1]:
                hit(icfg.deps, f, f"{loc} {mod} -> {n} depends'te yok")
    return out


def step_ithalat(root, icfg):
    top = git_top(root)
    prefix = git(root, "rev-parse", "--show-prefix").strip()
    files = [p[len(prefix):] for p in git(top, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
             .split("\0") if p and p.startswith(prefix)]
    return evaluate(icfg, root, files)


# ---------------------------------------------------------------- SELFTEST

ST_JS = r'''
import a from "./x";
import type { T } from '../b/types';
import {
  p,
  q as r,
} from "@/c/multi";
export * from "../d";
export { e } from "../e/index.js";
export type { F } from "../f";
import "../g/side";
const lazy = import("../h/lazy");
const req = require("../i/req");
const dyn = import(name);
// import z from "../bad1";
/* import z from "../bad2"; */
const s = "import z from '../bad3'";
const t = `export * from "../bad4"`;
const re1 = /"/; import k from "../k";
const div = a / 2; const y = b / 3; import m from "../m";
const A = () => <div>x</div>; const j = require("../j");
import ext from "react";
'''
ST_JS_WANT = {"src/a/x", "src/b/types", "src/c/multi", "src/d", "src/e/index", "src/f", "src/g/side",
              "src/h/lazy", "src/i/req", "src/k", "src/m", "src/j"}

ST_PY = r'''
import os, pkg.users.models as um
from pkg.payments import service
from . import helpers
from ..reports.gen import (
    build,
    render as rr,
)
from .. import shared
# import pkg.bad1
s = "import pkg.bad2"
"""
from pkg.bad3 import x
"""
def f():
    from pkg.lazy import thing
'''
ST_PY_WANT = {"os", "pkg/users/models", "pkg/payments", "pkg/payments/service", "pkg/donations",
              "pkg/donations/helpers", "pkg/reports/gen", "pkg/reports/gen/build",
              "pkg/reports/gen/render", "pkg", "pkg/shared", "pkg/lazy", "pkg/lazy/thing"}

ST_PHP = r'''<?php
namespace App\Donations;

use App\Users\User;
use App\Payments\{Gateway, Refund as R};
use function App\Support\helper;
use App\Shared\Money, App\Shared\Currency as Cur;
// use App\Bad1\X;
# use App\Bad2\X;
/* new \App\Bad3\X(); */

#[\App\Attrs\Audited]
final class Api
{
    use HasTenant;
    use \App\Traits\Loggable;

    public function run(): void
    {
        $s = new \App\Reports\Service();
        $t = Sub\Local::make();
        $v = R\Item::class;
        $u = User::class;
        $q = "App\\Bad4\\X";
        $h = <<<EOT
        new \App\Bad5\X();
        EOT;
        $f = function () use ($s) { return $s; };
        $e = new \Exception('x');
    }
}
'''
ST_PHP_WANT = {"app/Users/User", "app/Payments/Gateway", "app/Payments/Refund", "app/Support/helper",
               "app/Shared/Money", "app/Shared/Currency", "app/Attrs/Audited", "app/Donations/HasTenant",
               "app/Traits/Loggable", "app/Reports/Service", "app/Donations/Sub/Local",
               "app/Payments/Refund/Item"}

ST_GO = r'''package donations

import "github.com/acme/app/internal/users"
import pay "github.com/acme/app/internal/payments"
import (
	"fmt"
	rep "github.com/acme/app/internal/reports"
	_ "github.com/acme/app/internal/side"
	// "github.com/acme/app/internal/bad1"
	`github.com/acme/app/internal/raw`
)
var s = "import \"github.com/acme/app/internal/bad2\""
/* import "github.com/acme/app/internal/bad3" */
'''
ST_GO_WANT = {"internal/users", "internal/payments", "internal/reports", "internal/side", "internal/raw"}


def selftest():
    import contextlib
    import io
    fails, tmp = [], []
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def ok(cond, name):
        if not cond:
            fails.append(name)

    def raises(fn, code):
        try:
            fn()
        except GuardianError as e:
            return e.code == code
        return False

    def put(d, rel, body):
        write(d / rel, body)

    def project(files):
        d = Path(tempfile.mkdtemp(prefix="guardian-st-"))
        tmp.append(d)
        for rel, body in files.items():
            put(d, rel, body)
        git(d, "init", "-q", "-b", "main")
        return d

    def kontrol(d, profil="tam"):
        try:
            return run_kontrol(d, profil)
        except GuardianError as e:
            return 2, [f"ERROR {e.code} {e}"]

    def has(res, code, *needles):
        return res[0] == code and all(any(n in line for line in res[1]) for n in needles)

    try:
        # --- glob anlamı
        for p, s, want in [
            ("src/**", "src", True), ("src/**", "src/a/b.ts", True), ("src/**", "srcx/a", False),
            ("src/*", "src/a.ts", True), ("src/*", "src/a/b.ts", False), ("*.ts", "a/b.ts", False),
            ("**/*.ts", "b.ts", True), ("**/*.ts", "a/b/c.ts", True), ("**/*.ts", "a/b.tsx", False),
            ("src/**/controllers/**", "src/controllers/x.ts", True),
            ("src/**/controllers/**", "src/a/b/controllers/x.ts", True),
            ("src/**/controllers/**", "src/mycontrollers/x.ts", False),
            ("src/?.ts", "src/a.ts", True), ("src/?.ts", "src/ab.ts", False), ("src/**/**/x", "src/x", True),
            ("Src/**", "src/a", False), ("src/**", "", False), ("**", "any/depth/at/all", True),
        ]:
            ok(gmatch(p, s) == want, f"glob {p!r} ~ {s!r} = {want} olmalı")
        for bad in ("", "/src/**", "src/", "src//a", "src/a**", "src/{a,b}", "src/[ab]", "src\\a", "src/../a"):
            ok(raises(lambda: glob_rx(bad), "G002"), f"glob {bad!r} reddedilmeli")

        # --- yapılandırma
        base = '# guardian:v2\n[ajanlar]\ngelistirici = "codex exec"\n[bagimliliklar]\nnpm = ["react", "@types/*"]\n'
        ok(raises(lambda: Config("x = ["), "G001"), "geçersiz TOML -> G001")
        ok(raises(lambda: Config(base + "[limitlr]\n"), "G002"), "bilinmeyen tablo -> G002")
        ok(raises(lambda: Config(base.replace("npm", "nmp")), "G002"), "yazım hatalı ekosistem -> G002")
        ok(raises(lambda: Config(base + '[[adim]]\nad = "x"\nkomut = "y"\nprofil = "hzli"\n'), "G002"),
           "geçersiz profil -> G002")
        ok(raises(lambda: Config(base + '[[adim]]\nad = "x"\nkomut = "y"\nprofil = "tam"\nzaman = 3\n'), "G002"),
           "bilinmeyen adım anahtarı -> G002")
        ok(raises(lambda: Config(base + "[limitler]\nagents_md = 0\n"), "G002"), "sıfır limit -> G002")
        ok(Config(base + "[limitler]\nagents_md = 40\n").limit == {**LIMITS, "agents_md": 40}, "limit birleşimi")
        mods = ('[ithalat]\netkin = true\n[ithalat.resolve]\njs = { "@/" = "src/" }\nphp = { \'App\\\' = "app/" }\n'
                'go = { "github.com/acme/app/" = "" }\n[ithalat.modules.users]\npaths = ["src/users/**"]\n'
                'depends = []\n[ithalat.modules.donations]\npaths = ["src/donations/**"]\ndepends = ["users"]\n'
                '[ithalat.modules.reports]\npaths = ["src/reports/**"]\ndepends = ["donations"]\n')
        arch = ('[[ithalat.rules]]\nid = "ARCH-01"\ntype = "forbid_import"\nfrom = ["src/**/controllers/**"]\n'
                'targets = ["src/**/repositories/**"]\nmsg = "servis kullan"\n')
        ok(raises(lambda: Config(base + mods.replace('"src/reports/**"', '"src/users/x/**"')), "G003"),
           "iç içe modül -> G003")
        ok(raises(lambda: Config(base + mods.replace('"src/users/**"', '"src/users/*.ts"')), "G002"),
           "modül yolu dir/** olmalı")
        ok(raises(lambda: Config(base + mods + arch.replace("targets", "target")), "G002"), "kural yazım hatası")
        ok(raises(lambda: Config(base + mods + arch + 'except = [{ path = "src/**", reason = "r" }]\n'), "G002"),
           "geniş istisna -> G002")
        c = Config(base + mods).ithalat
        ok(c.module_of("src/users/x.ts") == "users" and c.module_of("src/usersx/a") is None, "yol -> modül")
        ok(Config(base).ithalat is None and Config(base + "[ithalat]\netkin = false\n").ithalat is None,
           "ithalat varsayılan kapalı")

        # --- çıkarıcılar (V1)
        for lang, path, text, want in (("js", "src/a/api.ts", ST_JS, ST_JS_WANT),
                                       ("py", "pkg/donations/api.py", ST_PY, ST_PY_WANT),
                                       ("php", "app/Donations/Api.php", ST_PHP, ST_PHP_WANT),
                                       ("go", "internal/donations/api.go", ST_GO, ST_GO_WANT)):
            got = {t for _, t in extract(lang, path, text, c)}
            ok(got == want, f"{lang} çıkarıcı: {sorted(got ^ want)}")
        ok(classify("a/b.swift") is None and classify("a/README.md") == "" and classify("a/x.d.ts") == "js",
           "dosya sınıflandırma")

        # --- manifest ayrıştırma
        d = project({})
        put(d, "composer.json", '{"require": {"php": "^8.2", "ext-json": "*", "laravel/framework": "^11"},'
                                '"require-dev": {"pestphp/pest": "^3"}}')
        put(d, "pubspec.yaml", "name: a\ndependencies:\n  flutter:\n    sdk: flutter\n  http: ^1.0 # yorum\n"
                               "dev_dependencies:\n  lints: any\nflutter:\n  uses-material-design: true\n")
        put(d, "requirements.txt", "# yorum\nDjango==5.0\n-r base.txt\nrequests_oauthlib>=1\n\n")
        ok(packages("composer", d / "composer.json") == ["laravel/framework", "pestphp/pest"], "composer paketleri")
        ok(packages("pub", d / "pubspec.yaml") == ["flutter", "http", "lints"], "pubspec paketleri")
        ok(packages("pip", d / "requirements.txt") == ["django", "requests-oauthlib"], "requirements paketleri")
        put(d, "package.json", "{bozuk")
        ok(raises(lambda: packages("npm", d / "package.json"), "G015"), "bozuk package.json -> G015")

        # --- kontrol: sessiz PASS, BLOCK, ERROR, önbellek
        sayac = Path(tempfile.mkdtemp(prefix="guardian-st-")) / "sayac"
        tmp.append(sayac.parent)
        step = (f'[[adim]]\nad = "lint"\nkomut = "{sys.executable} lint.py"\nprofil = "hizli"\n'
                f'[[adim]]\nad = "test"\nkomut = "{sys.executable} test.py"\nprofil = "tam"\n')
        files = {TOML_REL: base + step, ".gitignore": CACHE_REL + "/\n",
                 "lint.py": f"open({str(sayac)!r}, 'a').write('x')\n", "test.py": "import sys; sys.exit(0)\n",
                 "package.json": '{"dependencies": {"react": "18"}, "devDependencies": {"@types/node": "20"}}',
                 "AGENTS.md": MARK + "\n- kural\n", "CLAUDE.md": MARK + "\n", "DURUM.md": MARK + "\n",
                 "KARARLAR.md": MARK + "\n# KARARLAR\n- K-001 · 2026-10-17 · React — neden: tasarım\n"}
        d = project(files)
        ok(kontrol(d) == (0, []), "temiz ağaç -> sessiz PASS")
        ok(kontrol(d) == (0, []) and sayac.read_text() == "x", "değişmeyen ağaç -> önbellekten (adım çalışmaz)")
        ok(kontrol(d, "hizli") == (0, []) and sayac.read_text() == "xx", "profil başına ayrı önbellek")
        put(d, "package.json", '{"dependencies": {"react": "18", "lodash": "4"}}')
        ok(has(kontrol(d), 1, "BLOCK bagimliliklar", "'lodash' izin listesinde yok"), "izinsiz paket -> BLOCK")
        put(d, "package.json", files["package.json"])
        put(d, "test.py", "print('1 test başarısız')\nraise SystemExit(1)\n")
        ok(has(kontrol(d), 1, "BLOCK test", "1 test başarısız") and kontrol(d, "hizli")[0] == 0,
           "tam profil testi çalıştırır, hizli çalıştırmaz")
        put(d, "test.py", "import time; time.sleep(5)\n")
        put(d, TOML_REL, base + step + "zaman_asimi = 1\n")
        ok(has(kontrol(d), 2, "ERROR G014"), "zaman aşımı -> ERROR")
        put(d, TOML_REL, base + step.replace(f"{sys.executable} test.py", "olmayan-arac --x"))
        ok(has(kontrol(d), 2, "ERROR G013", "olmayan-arac"), "araç yok -> ERROR (asla PASS değil)")
        put(d, TOML_REL, base + step)
        put(d, "test.py", "import sys; sys.exit(0)\n")
        put(d, "AGENTS.md", MARK + "\n" + "- kural\n\n" * 60)
        ok(has(kontrol(d), 1, "BLOCK limitler", "AGENTS.md: 61 satır"), "AGENTS.md sınırı -> BLOCK")
        put(d, "AGENTS.md", files["AGENTS.md"])
        put(d, "KARARLAR.md", files["KARARLAR.md"] + "serbest not\n- K-002 · " + "x" * 200 + "\n")
        r = kontrol(d)
        ok(has(r, 1, "KARARLAR.md:4: biçim", "KARARLAR.md:5:"), f"KARARLAR biçim ve uzunluk -> BLOCK: {r}")
        put(d, "KARARLAR.md", files["KARARLAR.md"])
        (d / "DURUM.md").unlink()
        ok(has(kontrol(d), 2, "ERROR G018"), "eksik DURUM.md -> ERROR")
        put(d, "DURUM.md", MARK + "\n")
        put(d, TOML_REL, base + step + mods + arch)
        put(d, "src/users/repositories/repo.ts", "export const r = 1;\n")
        put(d, "src/donations/api.ts", 'import { r } from "../users/repositories/repo";\n')
        ok(kontrol(d) == (0, []), "ithalat: izinli bağımlılık -> PASS")
        put(d, "src/users/bad.ts", 'import x from "@/reports/r";\n')
        put(d, "src/donations/controllers/c.ts", 'import { r } from "@/users/repositories/repo";\n')
        ok(has(kontrol(d), 1, "BLOCK ithalat", "DEPS src/users/bad.ts:1 -> src/reports/r users -> reports",
               "ARCH-01 src/donations/controllers/c.ts:1"), "ithalat ihlalleri -> BLOCK")
        put(d, "src/users/v.swift", "import Foundation\n")
        ok(has(kontrol(d), 2, "ERROR G006"), "çıkarıcısız kapsam içi dosya -> ERROR")
        ok(kanarya_ithalat(Config(base + mods).ithalat) is True, "ithalat kanaryası BLOCK alır")
        nogit = Path(tempfile.mkdtemp(prefix="guardian-st-"))
        tmp.append(nogit)
        for rel, body in files.items():
            put(nogit, rel, body)
        ok(kontrol(nogit) == (0, []) and kontrol(nogit) == (0, []), "git yoksa önbelleksiz çalışır")

        # --- hızlı profil süre sınırı (doktor adimlar maddesi)
        ok(hizli_gecikti(21, False) is None and "sn sürdü (sınır 20)" in hizli_gecikti(21, True),
           "hizli sınır: önbellek dışı aşım uyarır; hit ve sınırlı süre sessiz")
        d = project(files)
        write_durum(d, {"asama": "aktif"})
        doktor_skip = ("kit", "zincir", "yer-tutucu", "claude-hook", "hook-calisiyor", "git-hook",
                       "kanarya", "ajanlar", "senkron", "limitler", "ci", "gitignore")
        global HIZLI_SINIR
        eski_sinir = HIZLI_SINIR
        try:
            HIZLI_SINIR = 0  # her gerçek çalıştırmayı aşım say
            r = run_doktor(d, doktor_skip)  # önbellek boş: kontrol çalışır ve ölçülür
            ok(r[0] == 1 and any("✗ adimlar" in x and "sn sürdü" in x for x in r[1]),
               "hizli profil sınır aşımı -> adimlar maddesi fail")
        finally:
            HIZLI_SINIR = eski_sinir
        r = run_doktor(d, doktor_skip)  # önbellek hit: ölçüm sayılmaz
        ok(r[0] == 0 and not any("sn sürdü" in x for x in r[1]), "sınır geri: önbellekten sessiz geçer")

        # --- hook ve çıkış kodu sözleşmesi
        d = project(files)
        write_durum(d, {"asama": "claude-bekliyor"})
        put(d, "package.json", '{"dependencies": {"lodash": "4"}}')
        with contextlib.redirect_stdout(io.StringIO()) as o:
            ok(run_hook(d, "{}") == 0 and not o.getvalue(), "kurulum sürerken hook sessiz")
        write_durum(d, {"asama": "aktif"})
        with contextlib.redirect_stderr(io.StringIO()) as e:
            ok(run_hook(d, "{}") == 2 and "lodash" in e.getvalue(), "hook: BLOCK -> exit 2 + stderr")
        with contextlib.redirect_stdout(io.StringIO()) as o:
            ok(run_hook(d, '{"stop_hook_active": true}') == 0 and "systemMessage" in o.getvalue(),
               "hook: tekrar denemede çözülmeyen BLOCK ajanı durdurur")
        (d / DURUM_REL).unlink()
        with contextlib.redirect_stdout(io.StringIO()) as o:
            ok(run_hook(d, "{}") == 0 and '"continue": false' in o.getvalue(), "hook: ERROR -> continue:false")
        global run_kontrol
        real = run_kontrol
        try:
            def boom(*a, **k):
                raise RuntimeError("boom")
            run_kontrol = boom
            write_durum(d, {"asama": "aktif"})
            with contextlib.redirect_stdout(io.StringIO()) as o:
                ok(main(["kontrol"]) == 2, "iç çökme -> exit 2")
                ok(main(["kontrol", "--bogus"]) == 2, "hatalı argüman -> exit 2")
                ok(run_hook(d, "{}") == 0 and "G009" in o.getvalue(), "hook çökmesi -> continue:false")
        finally:
            run_kontrol = real

        # --- settings birleştirme, envanter
        kit = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 .guardian/guardian.py hook"}]}]},
               "permissions": {"allow": ["A"], "deny": ["D"]}}
        cur = merge_settings({"model": "x", "permissions": {"allow": ["Z"]},
                              "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}}, kit)
        ok(cur["model"] == "x" and cur["permissions"] == {"allow": ["Z", "A"], "deny": ["D"]}
           and len(cur["hooks"]["Stop"]) == 2 and merge_settings(cur, kit) == cur, "settings birleştirme")
        d = project({"TASARIM/giris/code.html": "", "TASARIM/giris/screen.png": "", "TASARIM/liste.html": "",
                     "TASARIM/liste.png": "", "TASARIM/.gizli/x.png": "", "TASARIM/not.txt": ""})
        inv = envanter(d, find_tasarim(d))
        ok("- giris · html: TASARIM/giris/code.html · görsel: TASARIM/giris/screen.png" in inv
           and "- liste · html: TASARIM/liste.html · görsel: TASARIM/liste.png" in inv
           and screen_names(inv) == {"giris", "liste"}, f"tasarım envanteri: {inv}")

        # --- kaynak depo: kit bağlantıları ve şişme sınırları
        if KIT.is_dir():
            ks = json.loads((KIT / "claude-settings.json").read_text())
            ok(stop_commands(ks) and set(REQUIRED_ALLOW) <= set(ks["permissions"]["allow"])
               and set(REQUIRED_DENY) <= set(ks["permissions"]["deny"]), "kit settings doktor gereksinimlerini taşır")
            for src in [*STATIC.values(), *TEMPLATES.values(), "ci/guardian.yml", "claude-settings.json"]:
                ok(src is None or (KIT / src).is_file(), f"kit/{src} yok")
            for src in [s for s in [*STATIC.values(), *TEMPLATES.values()] if s and s.endswith(".md")]:
                ok(read(KIT / src, "").startswith(MARK), f"kit/{src} işaretle başlamalı")
            ok(code_lines(read(KIT / "CLAUDE.md", ""))[1:3] == ["@AGENTS.md", "@DURUM.md"], "kit CLAUDE.md içe aktarmaları")
            ok(Config(read(KIT / "guardian.toml", "")).gelistirici, "kit guardian.toml ayrıştırılır")
            kc = read(KIT / "CLAUDE.md", "")
            ok("git commit -m" in kc and "git diff HEAD" in kc and "run_in_background" in kc
               and "codex-son.md" in kc, "kit CLAUDE.md commit adımı ve arka plan talimatı")
            ok(not step_limitler(KIT, Config("")), "kit dosyaları satır sınırları içinde")
            for f, n in (("README.md", 200), ("guardian.py", 1800)):
                ok(len(read(GUARDIAN_DIR / f, "").splitlines()) <= n, f"{f} {n} satırı aşmamalı")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for t in tmp:
            shutil.rmtree(t, ignore_errors=True)
    for f in fails:
        print(f"SELFTEST FAIL {f}")
    return 2 if fails else 0


# ---------------------------------------------------------------- MAIN

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = PROJECT_ROOT
    try:
        cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
        if cmd == "--selftest" and not rest:
            return selftest()
        if cmd == "hook" and not rest:
            return run_hook(root, sys.stdin.read())
        if cmd == "kontrol" and rest in ([], ["--profil", "hizli"], ["--profil", "tam"]):
            code, lines = run_kontrol(root, rest[1] if rest else "tam")
        elif cmd == "doktor" and not rest:
            code, lines = run_doktor(root)
        elif not IN_PROJECT and not argv:
            return cmd_guardian(root)
        elif not IN_PROJECT and argv == ["incele"]:
            return cmd_incele(root)
        else:
            raise GuardianError("G002", f"hatalı kullanım\n{USAGE}")
        if lines:
            print("\n".join(lines))
        return code
    except GuardianError as e:
        print(f"ERROR {e.code} {e}\n{EPILOGUE}")
        return 2
    except KeyboardInterrupt:
        print("ERROR G009 kullanıcı tarafından kesildi")
        return 2
    except BaseException as e:  # çökme asla BLOCK (1) gibi okunmaz
        print(f"ERROR G009 iç hata: {type(e).__name__}: {e}\n{EPILOGUE}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
