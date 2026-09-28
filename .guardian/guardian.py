#!/usr/bin/env python3
"""Guardian V1: architecture guard for static import dependencies and module boundaries.

Usage:
  guardian.py check [--all] [--base REF]   L1: changed files | --all: full scan | --base: CI (L3)
  guardian.py context <module|path>        minimum architecture context
  guardian.py hook                         Claude Code Stop-hook adapter
  guardian.py onboard                      one-time: validate PROJECT.md, print the procedure
  guardian.py --selftest

Exit codes (core contract): 0 PASS, 1 BLOCK, 2 ERROR.

Project root: the parent of .guardian/ when this file lives there; otherwise the current
directory. CI relies on this to run the trusted base engine from outside the PR tree:
  git show origin/main:.guardian/guardian.py > "$RUNNER_TEMP/guardian.py"
  python3 "$RUNNER_TEMP/guardian.py" check --base origin/main

Scope: static imports only. Runtime DI, reflection, string-based lookup and dynamic
imports with non-literal arguments are outside V1's mechanical guarantee.

Glob semantics (paths are repo-relative, '/'-separated, case-sensitive, full match):
  *    any characters inside one segment, never '/'
  ?    exactly one character, never '/'
  **   whole segment only; zero or more segments. 'a/**' matches 'a' and everything below.
  Anything else is literal. '[]', '{}', '\\', empty/'.'/'..' segments and a leading or
  trailing '/' are a config ERROR.
Import targets are resolved to repo paths without a code extension (.ts, .py, ...), so
target globs should be directory based: 'src/**/repositories/**'.
"""
import sys

if sys.version_info < (3, 11):
    sys.stdout.write("ERROR G000 Python 3.11+ required\n")
    sys.exit(2)

import json
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import tomllib
from functools import lru_cache
from pathlib import Path

GUARDIAN_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = GUARDIAN_DIR.parent if GUARDIAN_DIR.name == ".guardian" else Path.cwd()
TOML_REL = ".guardian/guardian.toml"
MAX_LINES = 10
CONTEXT_BUDGET = 1500
MAX_GLOBAL = 5
EPILOGUE = "Human intervention required. Do not modify .guardian/."
USAGE = __doc__.split("\n\n")[1]


class GuardianError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- GLOB

@lru_cache(maxsize=None)
def glob_rx(pat):
    bad = GuardianError("G002", f"unsupported glob {pat!r}")
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


def glob_may_cover(pat, d):
    """True if pat can match directory d itself or some path below it."""
    ps = pat.split("/")
    for i, seg in enumerate(d.split("/")):
        if i >= len(ps):
            return False
        if ps[i] == "**":
            return True
        if not gmatch(ps[i], seg):
            return False
    return True


# ---------------------------------------------------------------- CONFIG

LANGS = ("js", "py", "php", "go")
RULE_KEYS = {
    "forbid_import": ({"id", "type", "from", "targets", "msg"}, {"severity", "except"}),
    "module_deps": ({"id", "type"}, {"severity", "msg", "except"}),
    "advisory": ({"id", "type", "msg"}, {"modules"}),
}


def _keys(d, req, opt, where):
    if not isinstance(d, dict):
        raise GuardianError("G002", f"{where}: expected a table")
    miss, extra = req - d.keys(), d.keys() - req - opt
    if miss or extra:
        raise GuardianError("G002", f"{where}: " + "; ".join(
            x for x in (miss and f"missing {sorted(miss)}", extra and f"unknown {sorted(extra)}") if x))


def _strs(v, where, allow_empty=False):
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v) or (not v and not allow_empty):
        raise GuardianError("G002", f"{where}: expected a {'' if allow_empty else 'non-empty '}list of strings")
    return v


def _str(v, where):
    if not isinstance(v, str) or not v.strip():
        raise GuardianError("G002", f"{where}: expected a non-empty string")
    return v


class Config:
    def __init__(self, text):
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise GuardianError("G001", f"guardian.toml invalid: {e}")
        _keys(data, set(), {"project", "resolve", "ignore", "modules", "rules"}, "guardian.toml")
        _keys(data.get("project", {}), set(), {"name"}, "[project]")
        self.ignore = _strs(data.get("ignore", []), "ignore", True)
        for g in self.ignore:
            glob_rx(g)

        res = data.get("resolve", {})
        _keys(res, set(), set(LANGS), "[resolve]")
        self.resolve = {}
        for lang in LANGS:
            table = res.get(lang, {"": ""} if lang == "py" else {})
            if not isinstance(table, dict) or not all(isinstance(v, str) for v in table.values()):
                raise GuardianError("G002", f"[resolve].{lang}: expected a table of strings")
            self.resolve[lang] = sorted(((k.replace("\\", "/"), v) for k, v in table.items()),
                                        key=lambda kv: -len(kv[0]))

        mods = data.get("modules", {})
        _keys(mods, set(), set(mods) if isinstance(mods, dict) else set(), "[modules]")
        self.modules, self.dirs = {}, []  # name -> (dirs, depends); [(dir, name)]
        for name, m in mods.items():
            where = f"[modules.{name}]"
            _keys(m, {"paths", "depends"}, set(), where)
            dirs = []
            for p in _strs(m["paths"], where + ".paths"):
                glob_rx(p)
                d = p[:-3]
                if not p.endswith("/**") or not d or any(c in d for c in "*?"):
                    raise GuardianError("G002", f"{where}: path {p!r} must be '<literal dir>/**'")
                dirs.append(d)
            self.modules[name] = (dirs, _strs(m["depends"], where + ".depends", True))
            self.dirs += [(d, name) for d in dirs]
        for name, (_, deps) in self.modules.items():
            for dep in deps:
                if dep not in self.modules or dep == name:
                    raise GuardianError("G002", f"[modules.{name}].depends: invalid module {dep!r}")
        for i, (a, na) in enumerate(self.dirs):
            for b, nb in self.dirs[i + 1:]:
                if na != nb and (a == b or a.startswith(b + "/") or b.startswith(a + "/")):
                    raise GuardianError("G003", f"module paths overlap: {na} ({a}) / {nb} ({b})")
        self.roots = {d.split("/")[0] for d, _ in self.dirs}

        self.forbid, self.advisory, self.deps = [], [], None
        rules, ids = data.get("rules", []), set()
        if not isinstance(rules, list):
            raise GuardianError("G002", "rules: expected [[rules]]")
        for r in rules:
            if not isinstance(r, dict) or r.get("type") not in RULE_KEYS:
                raise GuardianError("G002", f"rule {r.get('id') if isinstance(r, dict) else r!r}: unknown type")
            where = f"rule {r.get('id', '?')}"
            _keys(r, *RULE_KEYS[r["type"]], where)
            if _str(r["id"], where + ".id") in ids:
                raise GuardianError("G002", f"{where}: duplicate id")
            ids.add(r["id"])
            if r["type"] == "advisory":
                _str(r["msg"], where + ".msg")
                for m in _strs(r["modules"], where + ".modules") if "modules" in r else []:
                    if m not in self.modules:
                        raise GuardianError("G002", f"{where}: unknown module {m!r}")
                self.advisory.append(r)
                continue
            if r.setdefault("severity", "block") not in ("block", "warn"):
                raise GuardianError("G002", f"{where}: severity must be 'block' or 'warn'")
            r["except"] = self._exceptions(r.get("except", []), where)
            if r["type"] == "forbid_import":
                _str(r["msg"], where + ".msg")
                for g in _strs(r["from"], where + ".from") + _strs(r["targets"], where + ".targets"):
                    glob_rx(g)
                self.forbid.append(r)
            elif self.deps:
                raise GuardianError("G002", f"{where}: only one module_deps rule allowed")
            else:
                _str(r.setdefault("msg", "module dependency outside depends"), where + ".msg")
                self.deps = r
        if self.deps is None:
            self.deps = {"id": "DEPS", "type": "module_deps", "severity": "block",
                         "msg": "module dependency outside depends", "except": []}

    def _exceptions(self, ex, where):
        if not isinstance(ex, list):
            raise GuardianError("G002", f"{where}.except: expected a list")
        out = []
        for e in ex:
            _keys(e, {"path", "reason"}, set(), where + ".except")
            _str(e["reason"], where + ".except.reason")
            glob_rx(_str(e["path"], where + ".except.path"))
            for d, _ in self.dirs:
                if gmatch(e["path"], d) or gmatch(e["path"], d + "/_guardian_probe_/_probe_"):
                    raise GuardianError("G002", f"{where}: exception {e['path']!r} too broad (covers {d})")
            out.append(e["path"])
        return out

    def module_of(self, path):
        for d, name in self.dirs:
            if path == d or path.startswith(d + "/"):
                return name
        return None


def load_config(root):
    try:
        return (root / TOML_REL).read_text(encoding="utf-8")
    except OSError as e:
        raise GuardianError("G001", f"cannot read {TOML_REL}: {e.strerror}")


# ---------------------------------------------------------------- IMPORT EXTRACTION

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
    """Language key, '' for known non-code files, None for unsupported code/unknown."""
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
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_KW = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void",
             "throw", "instanceof", "yield", "await"}


def _blank(s):
    return "\n".join(" " * len(p) for p in s.split("\n"))


def scan(text, lang):
    """Blank comments and non-literal strings (offsets preserved). Literal strings become
    \\x01...\\x01 placeholders; strings[offset] holds their raw content."""
    rx, out, strings, pos = TOKENS[lang], [], {}, 0
    while m := rx.search(text, pos):
        kind = next(k for k in _KINDS if k in rx.groupindex and m.group(k) is not None)
        s, e, tok = m.start(), m.end(), m.group()
        if kind == "r":  # JS: regex literal or division?
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
    """(fqcn, alias) pairs of a top-level use statement body."""
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
            if depth > imp_depth:  # trait use (closure `use (` does not match)
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


def extract(lang, path, text, cfg):
    """[(line, target_path)] of internal static imports; external imports are dropped."""
    code, strings = scan(text, lang)
    seen, out = set(), []
    for pos, tgt in EXTRACTORS[lang](code, strings, path, cfg.resolve[lang]):
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


# ---------------------------------------------------------------- RULE CHECKS

class Report:
    def __init__(self):
        self.errors, self.blocks, self.warns = [], [], []


def evaluate(cfg, root, files, rep, tag=""):
    """Check files against cfg; returns the set of (rule_id, except_path) that were used."""
    used = set()

    def hit(rule, f, text):
        ex = next((p for p in rule["except"] if gmatch(p, f)), None)
        if ex is not None:
            used.add((rule["id"], ex))
            return
        sev = rule["severity"]
        (rep.blocks if sev == "block" else rep.warns).append(
            (rule["id"], f"{sev.upper()} {rule['id']}{tag} {text}"))

    for f in sorted(set(files)):
        full = root / f
        if not full.is_file() or any(gmatch(g, f) for g in cfg.ignore):
            continue
        mod = cfg.module_of(f)
        forbid = [r for r in cfg.forbid if any(gmatch(g, f) for g in r["from"])]
        lang = classify(f)
        if mod is None and lang and f.split("/")[0] in cfg.roots:
            rep.warns.append(("W001", f"WARN W001{tag} {f} maps to no module"))
        if mod is None and not forbid or lang == "":
            continue
        if lang is None:
            rep.errors.append(f"ERROR G006 {f}: no import extractor for this file type "
                              "(it is in an enforced rule's scope)")
            continue
        if lang in ("php", "go") and not cfg.resolve[lang]:
            rep.errors.append(f"ERROR G007 {f}: [resolve].{lang} is required")
            continue
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            rep.errors.append(f"ERROR G008 {f}: {e.strerror}")
            continue
        for line, tgt in extract(lang, f, text, cfg):
            loc = f"{f}:{line} -> {tgt}"
            for r in forbid:
                if any(gmatch(t, tgt) for t in r["targets"]):
                    hit(r, f, f"{loc} {r['msg']}")
            n = cfg.module_of(tgt) if mod else None
            if n and n != mod and n not in cfg.modules[mod][1]:
                hit(cfg.deps, f, f"{loc} {mod} -> {n} not in depends")
    return used


def full_scan_warns(cfg, used, rep, tag=""):
    for r in cfg.forbid + [cfg.deps]:
        for p in r["except"]:
            if (r["id"], p) not in used:
                rep.warns.append(("W002", f"WARN W002{tag} {r['id']} exception {p} covers no violation"))
    n = sum(1 for r in cfg.advisory if not r.get("modules"))
    if n > MAX_GLOBAL:
        rep.warns.append(("W003", f"WARN W003{tag} {n} global advisories (target <= {MAX_GLOBAL})"))


# ---------------------------------------------------------------- GIT

def git(top, *args):
    try:
        p = subprocess.run(["git", "-C", str(top), *args], capture_output=True)
    except OSError as e:
        raise GuardianError("G004", f"git unavailable: {e.strerror}")
    if p.returncode:
        raise GuardianError("G004", f"git {args[0]} failed: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout.decode("utf-8", "replace")


class Repo:
    def __init__(self, root):
        top, _, prefix = git(root, "rev-parse", "--show-toplevel", "--show-prefix").partition("\n")
        self.top, self.prefix = top.strip(), prefix.strip()

    def _rel(self, paths):
        n = len(self.prefix)
        return [p[n:] for p in paths if p and p.startswith(self.prefix)]

    def changed(self):
        toks, files, i = git(self.top, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0"), [], 0
        while i < len(toks):
            t = toks[i]
            i += 1
            if len(t) > 3:
                files.append(t[3:])
                if "R" in t[:2] or "C" in t[:2]:
                    i += 1  # skip rename/copy source
        return self._rel(files)

    def all_files(self):
        return self._rel(git(self.top, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0"))

    def merge_base(self, base):
        try:
            return git(self.top, "merge-base", base, "HEAD").strip()
        except GuardianError:
            raise GuardianError("G005", f"merge-base of {base} and HEAD not found "
                                "(shallow clone? use full history, e.g. fetch-depth: 0)")

    def diff(self, mb):
        return self._rel(git(self.top, "diff", "--name-only", "-z", mb, "HEAD").split("\0"))

    def show(self, ref, rel):
        try:
            return git(self.top, "show", f"{ref}:{self.prefix}{rel}")
        except GuardianError:
            return None


# ---------------------------------------------------------------- OUTPUT

def render(rep):
    lines = rep.errors[:MAX_LINES]
    if len(rep.errors) > MAX_LINES:
        lines.append(f"+{len(rep.errors) - MAX_LINES} more ERROR")
    for kind, items in (("BLOCK", rep.blocks), ("WARN", rep.warns)):
        items = sorted(set(items))
        lines += [t for _, t in items[:MAX_LINES]]
        if len(items) > MAX_LINES:
            lines.append(f"+{len(items) - MAX_LINES} more {kind}")
    if rep.errors:
        lines.append(EPILOGUE)
    return (2 if rep.errors else 1 if rep.blocks else 0), lines


# ---------------------------------------------------------------- COMMANDS

def run_check(root, all_=False, base=None):
    rep, head_text = Report(), load_config(root)
    repo = Repo(root)
    if base is None:
        cfg = configured(Config(head_text), root)
        files = repo.all_files() if all_ else repo.changed()
        if not all_ and TOML_REL in files:  # rule change: check the full rule scope
            files, all_ = repo.all_files(), True
        used = evaluate(cfg, root, files, rep)
        if all_:
            full_scan_warns(cfg, used, rep)
        return render(rep)

    mb = repo.merge_base(base)
    base_text = repo.show(base, TOML_REL)
    cfg = Config(base_text) if base_text is not None else None
    if cfg is None or not cfg.modules:  # install/onboarding PR: no base policy to protect yet
        rep.warns.append(("W004", f"WARN W004 {base} has no configured Project Brain; "
                                  "using head rules on the full scope"))
        cfg = configured(Config(head_text), root)
        full_scan_warns(cfg, evaluate(cfg, root, repo.all_files(), rep), rep)
        return render(rep)
    used = evaluate(cfg, root, repo.all_files() if all_ else repo.diff(mb), rep)
    if all_:
        full_scan_warns(cfg, used, rep)
    if base_text != head_text:  # rules changed in this PR
        hcfg = configured(Config(head_text), root)
        full_scan_warns(hcfg, evaluate(hcfg, root, repo.all_files(), rep, "[head]"), rep, "[head]")
    return render(rep)


PROJECT_MD = "PROJECT.md"
PROJECT_SECTIONS = {  # section -> accepted headings (case-insensitive, exact)
    "Purpose": ("purpose", "amaç"),
    "Stack": ("stack", "teknoloji"),
    "Source roots": ("source roots", "kaynak dizinleri"),
    "Architecture": ("architecture", "mimari"),
    "Modules": ("modules", "modüller"),
    "Dependencies": ("dependencies", "bağımlılıklar"),
    "Import aliases": ("import aliases", "import alias"),
    "Rules": ("rules", "kurallar"),
}


def configured(cfg, root):
    """An empty Project Brain checks nothing, so it must never read as PASS."""
    if not cfg.modules:
        hint = ("Onboarding: python3 .guardian/guardian.py onboard" if (root / PROJECT_MD).is_file()
                else f"{PROJECT_MD} is missing: create it (README, step 2), then run onboard")
        raise GuardianError("G010", f"Project Brain not configured (no modules). {hint}")
    return cfg


def check_project_md(root):
    """Onboarding precondition: PROJECT.md exists and every required section has content."""
    try:
        text = (root / PROJECT_MD).read_text(encoding="utf-8")
    except OSError:
        raise GuardianError("G011", f"{PROJECT_MD} not found at the project root. It is the source of "
                            "the project's intended architecture; create it (README, step 2).")
    found, cur = {}, None
    for line in re.sub(r"<!--.*?-->", "", text, flags=re.S).splitlines():
        if m := re.match(r"#{1,6}\s+(.*)", line):
            h = re.sub(r"^[\d.\s]+", "", m.group(1)).strip().rstrip(":").strip().lower()
            cur = next((k for k, names in PROJECT_SECTIONS.items() if h in names), None)
            found.setdefault(cur, False)
        elif cur and line.strip():
            found[cur] = True
    missing = [f"{k} ({'/'.join(v)})" for k, v in PROJECT_SECTIONS.items() if not found.get(k)]
    if missing:
        raise GuardianError("G012", f"{PROJECT_MD} incomplete; missing or empty sections: "
                            + ", ".join(missing) + ". Write 'none' where nothing applies.")


ONBOARD = """\
GUARDIAN ONBOARDING (one-time). Source of intent: PROJECT.md. Do NOT scan source code to
discover the architecture; existing code is not the intended architecture.
1. Read PROJECT.md once. Take only what Guardian needs: source roots, modules (dir), allowed
   module dependencies, import aliases/prefixes, rules that are import boundaries.
   Purpose/stack only decide which [resolve] languages matter.
2. Map: module -> [modules.X] paths = ["<dir>/**"]; allowed deps -> depends; import-boundary
   rules -> forbid_import; other stated decisions -> short advisory. Nothing else: no
   descriptions, no tech inventory.
3. Do not guess. Anything PROJECT.md leaves unclear (dir, dependency, alias, a rule that is not
   an import boundary) -> ask the human in ONE short list.
4. Show the proposed guardian.toml. Write .guardian/guardian.toml only after the human
   approves. Keep enforced rules at severity = "warn" (calibration).
5. Run `python3 .guardian/guardian.py check --all`. ERROR -> fix the Brain. WARN = current code
   differs from PROJECT.md: report it; never loosen the Brain to match the code.
6. Done. Normal work uses guardian.toml only; do not re-read PROJECT.md for Guardian.
   Later changes follow the rule-change process."""


def run_onboard(root):
    check_project_md(root)
    if Config(load_config(root)).modules:
        return 0, ["Project Brain already configured; onboarding is done. "
                   "Changes follow the rule-change process."]
    return 0, ONBOARD.split("\n")


def run_context(root, target):
    cfg = configured(Config(load_config(root)), root)
    target = target.strip().removeprefix("./")
    if target in cfg.modules:
        mod, path = target, None
    elif "/" in target or "." in target:
        path = _norm(target) or ""
        mod = cfg.module_of(path)
    else:
        raise GuardianError("G002", f"unknown module {target!r}")
    lines = []
    if mod:
        dirs, deps = cfg.modules[mod]
        lines.append(f"module {mod}: {' '.join(d + '/**' for d in dirs)}")
        lines.append(f"depends [enforced {cfg.deps['id']}]: {', '.join(deps) or '(none)'}")
    else:
        lines.append(f"{path}: no module")
    for r in cfg.forbid:
        if path is not None:
            applies = any(gmatch(g, path) for g in r["from"])
        else:
            applies = any(glob_may_cover(g, d) for g in r["from"] for d in cfg.modules[mod][0])
        if applies:
            lines.append(f"{r['id']} [enforced{' warn' if r['severity'] == 'warn' else ''}] {r['msg']}")
    lines += [f"{r['id']} [advisory] {r['msg']}" for r in cfg.advisory
              if mod and mod in r.get("modules", ())]
    lines += [f"{r['id']} [advisory, global] {r['msg']}" for r in cfg.advisory if not r.get("modules")]
    out, size = [], 0
    for i, line in enumerate(lines):
        if size + len(line) + 1 > CONTEXT_BUDGET:
            out.append(f"(+{len(lines) - i} omitted)")
            break
        out.append(line)
        size += len(line) + 1
    return 0, out


def run_hook(root, stdin_text):
    """Claude Code Stop hook: BLOCK -> exit 2 (agent fixes); unresolved BLOCK on the
    retry -> let the agent stop; ERROR -> stop the agent, no retry."""
    try:
        try:
            active = bool(json.loads(stdin_text or "{}").get("stop_hook_active"))
        except (ValueError, AttributeError):
            active = False
        try:
            code, lines = run_check(root)
        except GuardianError as e:
            code, lines = 2, [f"ERROR {e.code} {e}", EPILOGUE]
        out = "\n".join(lines)
        if code == 0:
            return 0
        if code == 1 and not active:
            sys.stderr.write(out + "\nFix the application code. If the correct fix needs a new module "
                             "dependency: do not work around it; stop and propose the rule change.\n")
            return 2
        if code == 1:
            print(json.dumps({"systemMessage": "Guardian BLOCK unresolved:\n" + out}))
            return 0
    except BaseException as e:  # never let a crash re-enter the agent loop
        out = f"ERROR G009 internal: {type(e).__name__}: {e}\n{EPILOGUE}"
    print(json.dumps({"continue": False, "stopReason": "Guardian ERROR\n" + out}))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = PROJECT_ROOT
    try:
        cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
        if cmd == "--selftest" and not rest:
            return selftest()
        if cmd == "hook" and not rest:
            return run_hook(root, sys.stdin.read())
        if cmd == "check":
            all_, base, it = False, None, iter(rest)
            for a in it:
                if a == "--all":
                    all_ = True
                elif a == "--base" and (base := next(it, None)):
                    pass
                else:
                    raise GuardianError("G002", f"bad argument {a!r}\n{USAGE}")
            code, lines = run_check(root, all_, base)
        elif cmd == "onboard" and not rest:
            code, lines = run_onboard(root)
        elif cmd == "context" and len(rest) == 1:
            code, lines = run_context(root, rest[0])
        else:
            raise GuardianError("G002", f"bad usage\n{USAGE}")
        if lines:
            print("\n".join(lines))
        return code
    except GuardianError as e:
        print(f"ERROR {e.code} {e}\n{EPILOGUE}")
        return 2
    except BaseException as e:  # a crash must never read as BLOCK (exit 1)
        print(f"ERROR G009 internal: {type(e).__name__}: {e}\n{EPILOGUE}")
        return 2


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
import ext from "react";
'''
ST_JS_WANT = {"src/a/x", "src/b/types", "src/c/multi", "src/d", "src/e/index", "src/f", "src/g/side",
              "src/h/lazy", "src/i/req", "src/k", "src/m"}

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

ST_TOML = '''
[resolve]
js = { "@/" = "src/" }
[modules.users]
paths = ["src/users/**"]
depends = []
[modules.donations]
paths = ["src/donations/**"]
depends = ["users"]
[modules.reports]
paths = ["src/reports/**"]
depends = ["donations"]
'''
ST_ARCH = '''
[[rules]]
id = "ARCH-01"
type = "forbid_import"
from = ["src/**/controllers/**"]
targets = ["src/**/repositories/**"]
severity = "{sev}"
msg = "use a service"
{exc}
'''


def selftest():
    fails, tmp = [], []
    env_keys = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")
    saved = {k: os.environ.get(k) for k in env_keys}
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

    def targets(lang, path, text, cfg):
        return {t for _, t in extract(lang, path, text, cfg)}

    def project(files, toml):
        d = Path(tempfile.mkdtemp(prefix="guardian-st-"))
        tmp.append(d)
        files = {TOML_REL: toml, **files}
        for rel, body in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(body)
        g(d, "init", "-q", "-b", "main")
        commit(d, "init")
        return d

    def g(d, *args):
        return git(d, "-c", "user.name=st", "-c", "user.email=st@st", "-c", "commit.gpgsign=false", *args)

    def commit(d, msg):
        g(d, "add", "-A")
        g(d, "commit", "-qm", msg, "--allow-empty")

    def put(d, rel, body):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)

    def check(d, **kw):
        try:
            return run_check(d, **kw)
        except GuardianError as e:
            return 2, [f"ERROR {e.code} {e}"]

    def has(res, code, *needles):
        return res[0] == code and all(any(n in line for line in res[1]) for n in needles)

    try:
        # --- glob semantics
        for p, s, want in [
            ("src/**", "src", True), ("src/**", "src/a/b.ts", True), ("src/**", "srcx/a", False),
            ("src/*", "src/a.ts", True), ("src/*", "src/a/b.ts", False), ("*.ts", "a/b.ts", False),
            ("**/*.ts", "b.ts", True), ("**/*.ts", "a/b/c.ts", True), ("**/*.ts", "a/b.tsx", False),
            ("src/**/controllers/**", "src/controllers/x.ts", True),
            ("src/**/controllers/**", "src/a/b/controllers/x.ts", True),
            ("src/**/controllers/**", "src/a/controllersx/x.ts", False),
            ("src/**/controllers/**", "src/mycontrollers/x.ts", False),
            ("src/?.ts", "src/a.ts", True), ("src/?.ts", "src/ab.ts", False), ("src/?", "src//", False),
            ("src/a.ts", "src/a.ts", True), ("src/a.ts", "src/aXts", False), ("src/**/**/x", "src/x", True),
            ("Src/**", "src/a", False), ("src/**", "", False), ("**", "any/depth/at/all", True),
            ("src/*/api", "src/donations/api", True), ("src/*/api", "src/a/b/api", False),
        ]:
            ok(gmatch(p, s) == want, f"glob {p!r} ~ {s!r} should be {want}")
        for bad in ("", "/src/**", "src/", "src//a", "src/a**", "src/{a,b}", "src/[ab]", "src\\a",
                    "src/../a", "./src"):
            ok(raises(lambda: glob_rx(bad), "G002"), f"glob {bad!r} should be rejected")
        ok(glob_may_cover("src/**/controllers/**", "src/donations"), "glob_may_cover positive")
        ok(not glob_may_cover("lib/**", "src/donations"), "glob_may_cover negative")

        # --- config / module map
        mod_a = '[modules.a]\npaths = ["src/a/**"]\ndepends = []\n'
        fb = '[[rules]]\nid = "X"\ntype = "forbid_import"\nfrom = ["src/**"]\ntargets = ["x/**"]\nmsg = "m"\n'
        ok(raises(lambda: Config("x = ["), "G001"), "invalid TOML -> G001")
        ok(raises(lambda: Config(mod_a + '[modules.b]\npaths = ["src/a/sub/**"]\ndepends = []\n'), "G003"),
           "nested module paths -> G003")
        ok(raises(lambda: Config(mod_a + '[modules.b]\npaths = ["src/a/**"]\ndepends = []\n'), "G003"),
           "same module path -> G003")
        ok(raises(lambda: Config('[modules.a]\npaths = ["src/a/*.ts"]\ndepends = []\n'), "G002"),
           "module path must be dir/**")
        ok(raises(lambda: Config('[modules.a]\npaths = ["src/a/**"]\n'), "G002"), "depends required")
        ok(raises(lambda: Config(mod_a + fb.replace("targets", "target")), "G002"), "typo key -> G002")
        ok(raises(lambda: Config(mod_a + fb + 'except = [{ path = "src/**", reason = "r" }]\n'), "G002"),
           "broad exception -> G002")
        ok(raises(lambda: Config(mod_a + fb + 'except = [{ path = "src/a", reason = "r" }]\n'), "G002"),
           "exception equal to module root -> G002")
        ok(raises(lambda: Config(mod_a + fb + 'except = [{ path = "src/a/x.ts", reason = " " }]\n'), "G002"),
           "exception without reason -> G002")
        ok(raises(lambda: Config(mod_a + fb.replace('"X"', '"Y"') + fb.replace('"X"', '"Y"')), "G002"),
           "duplicate rule id -> G002")
        c = Config(mod_a + '[modules.b]\npaths = ["src/b/**"]\ndepends = ["a"]\n'
                   '[resolve]\njs = { "@/" = "src/" }\nphp = { \'App\\\' = "app/" }\n'
                   'go = { "github.com/acme/app/" = "" }\n')
        ok(c.module_of("src/a/x.ts") == "a" and c.module_of("src/b") == "b"
           and c.module_of("src/ab/x.ts") is None, "path -> module mapping")

        # --- extractors
        got = targets("js", "src/a/api.ts", ST_JS, c)
        ok(got == ST_JS_WANT, f"js extractor: {sorted(got ^ ST_JS_WANT)}")
        got = targets("py", "pkg/donations/api.py", ST_PY, c)
        ok(got == ST_PY_WANT, f"py extractor: {sorted(got ^ ST_PY_WANT)}")
        got = targets("php", "app/Donations/Api.php", ST_PHP, c)
        ok(got == ST_PHP_WANT, f"php extractor: {sorted(got ^ ST_PHP_WANT)}")
        got = targets("go", "internal/donations/api.go", ST_GO, c)
        ok(got == ST_GO_WANT, f"go extractor: {sorted(got ^ ST_GO_WANT)}")
        ok(classify("a/b.swift") is None and classify("a/README.md") == "" and classify("a/x.d.ts") == "js",
           "file classification")

        # --- L1 rules on a git repo
        toml = ST_TOML + ST_ARCH.format(sev="block", exc="")
        base_files = {"src/users/repositories/repo.ts": "export const r = 1;\n",
                      "src/donations/api.ts": 'import { r } from "../users/repositories/repo";\n',
                      "src/reports/r.ts": 'import * as d from "@/donations/api";\n'}
        d = project(base_files, toml)
        ok(check(d) == (0, []), "clean tree -> silent PASS")
        ok(check(d, all_=True) == (0, []), "clean full scan -> silent PASS")
        put(d, "src/donations/api.ts", base_files["src/donations/api.ts"] + 'import x from "../reports/r";\n')
        ok(has(check(d), 1, "BLOCK DEPS src/donations/api.ts:2 -> src/reports/r donations -> reports"),
           "module_deps violation (modified) -> BLOCK")
        g(d, "checkout", "-q", "--", ".")
        put(d, "src/donations/controllers/c.ts", 'import { r } from "@/users/repositories/repo";\n')
        ok(has(check(d), 1, "BLOCK ARCH-01 src/donations/controllers/c.ts:1"), "forbid_import (untracked) -> BLOCK")
        g(d, "add", "-A")
        ok(has(check(d), 1, "ARCH-01"), "forbid_import (staged) -> BLOCK")
        g(d, "reset", "-q", "--hard")
        put(d, "src/donations/view.swift", "import Foundation\n")
        ok(has(check(d), 2, "ERROR G006 src/donations/view.swift"), "unsupported extractor -> ERROR")
        (d / "src/donations/view.swift").unlink()
        put(d, "src/donations/README.md", "import x from '../reports/r'\n")
        ok(check(d) == (0, []), "non-code file ignored")
        put(d, "src/misc/x.ts", 'import x from "../reports/r";\n')
        ok(has(check(d), 0, "WARN W001 src/misc/x.ts"), "unmapped file -> WARN")
        g(d, "clean", "-qfd")
        g(d, "mv", "src/donations/api.ts", "src/donations/api2.ts")
        put(d, "src/donations/api2.ts", 'import x from "../reports/r";\n')
        ok(has(check(d), 1, "src/donations/api2.ts:1"), "renamed file checked")
        g(d, "reset", "-q", "--hard")
        g(d, "rm", "-q", "src/reports/r.ts")
        ok(check(d) == (0, []), "deleted file -> PASS")
        g(d, "reset", "-q", "--hard")

        # warn severity, exceptions
        put(d, "src/donations/controllers/c.ts", 'import { r } from "../../users/repositories/repo";\n')
        put(d, TOML_REL, ST_TOML + ST_ARCH.format(sev="warn", exc=""))
        ok(has(check(d), 0, "WARN ARCH-01"), "severity warn -> exit 0 + WARN")
        exc = 'except = [{ path = "src/donations/controllers/c.ts", reason = "legacy" }]'
        put(d, TOML_REL, ST_TOML + ST_ARCH.format(sev="block", exc=exc))
        ok(check(d) == (0, []), "exception honoured")
        (d / "src/donations/controllers/c.ts").unlink()
        ok(has(check(d, all_=True), 0, "WARN W002 ARCH-01 exception"), "unused exception -> WARN")
        g(d, "reset", "-q", "--hard")

        # rule change in L1 -> full rule scope (old, untouched violation found)
        d2 = project({**base_files, "src/donations/controllers/old.ts":
                      'import { r } from "@/users/repositories/repo";\n'}, ST_TOML)
        ok(check(d2) == (0, []), "no rule yet -> PASS")
        put(d2, TOML_REL, toml)
        ok(has(check(d2), 1, "BLOCK ARCH-01 src/donations/controllers/old.ts"), "toml change -> full scope check")
        put(d2, TOML_REL, "x = [")
        ok(has(check(d2), 2, "G001"), "invalid TOML -> ERROR")

        # --- L3: base rules, relaxation, tightening, merge-base
        g(d, "checkout", "-q", "-b", "feat")
        put(d, "src/donations/api.ts", 'import x from "../reports/r";\n')
        put(d, TOML_REL, toml.replace('depends = ["users"]', 'depends = ["users", "reports"]'))
        commit(d, "relax")
        ok(has(check(d, base="main"), 1, "BLOCK DEPS src/donations/api.ts"), "relaxed PR rules ignored (base rules)")
        ok(check(d) == (0, []), "sanity: head rules locally pass")
        g(d2, "checkout", "-q", "--", ".")
        g(d2, "checkout", "-q", "-b", "tighten")
        put(d2, TOML_REL, toml)
        commit(d2, "tighten")
        ok(has(check(d2, base="main"), 1, "BLOCK ARCH-01[head] src/donations/controllers/old.ts"),
           "tightened PR rules checked on full scope")
        g(d, "checkout", "-q", "--orphan", "other")
        commit(d, "orphan")
        g(d, "checkout", "-q", "feat")
        ok(has(check(d, base="other"), 2, "G005"), "missing merge-base -> ERROR")
        ok(has(check(d, base="nope"), 2, "G005"), "unknown base ref -> ERROR")
        nogit = Path(tempfile.mkdtemp(prefix="guardian-st-"))
        tmp.append(nogit)
        put(nogit, TOML_REL, toml)
        ok(has(check(nogit), 2, "G004"), "no git repo -> ERROR")

        # --- onboarding from PROJECT.md: fail closed until the Brain exists
        d4 = project({**base_files, "src/users/bad.ts": 'import d from "../donations/api";\n'},
                     "[resolve]\njs = {}\n")
        ok(has(check(d4), 2, "ERROR G010", "PROJECT.md is missing"), "no Brain, no PROJECT.md -> G010")
        ok(raises(lambda: run_context(d4, "src/a.ts"), "G010"), "context on empty Brain -> G010")
        ok(raises(lambda: run_onboard(d4), "G011"), "onboard without PROJECT.md -> G011")
        put(d4, PROJECT_MD, "")
        ok(raises(lambda: run_onboard(d4), "G012"), "empty PROJECT.md -> G012")
        full = "".join(f"## {k}\n{'none' if k in ('Rules', 'Import aliases') else 'x'}\n"
                       for k in PROJECT_SECTIONS)
        put(d4, PROJECT_MD, full.replace("## Modules\nx", "## Modules\n<!-- fill me -->"))
        try:
            run_onboard(d4)
            ok(False, "unfilled Modules section -> G012")
        except GuardianError as e:
            ok(e.code == "G012" and "Modules" in str(e) and "Purpose" not in str(e),
               f"G012 names only the missing section: {e}")
        put(d4, PROJECT_MD, full.replace("## Architecture", "## 1. Mimari:"))
        ob = run_onboard(d4)
        ok(ob[0] == 0 and ob[1][0].startswith("GUARDIAN ONBOARDING"), "complete PROJECT.md -> procedure")
        ok(has(check(d4), 2, "ERROR G010", "Onboarding: python3"), "onboard alone does not activate Guardian")
        g(d4, "checkout", "-q", "-b", "onboard")
        put(d4, TOML_REL, ST_TOML)
        commit(d4, "onboard")
        ok(run_onboard(d4)[1][0].startswith("Project Brain already configured"), "onboarding is one-time")
        ok(has(check(d4, all_=True), 1, "BLOCK DEPS src/users/bad.ts"), "approved Brain -> normal checks")
        ok(has(check(d4, base="main"), 1, "WARN W004", "BLOCK DEPS src/users/bad.ts"),
           "install PR: unconfigured base -> head rules on the full scope")

        # --- CI engine protection: a PR that neuters its own guardian.py is still checked
        d3 = project({**base_files, ".guardian/guardian.py": Path(__file__).read_text()}, toml)
        g(d3, "checkout", "-q", "-b", "pr")
        put(d3, ".guardian/guardian.py", "import sys\nsys.exit(0)\n")
        put(d3, "src/donations/api.ts", 'import x from "../reports/r";\n')
        commit(d3, "bypass")
        trusted = Path(tempfile.mkdtemp(prefix="guardian-st-"))
        tmp.append(trusted)
        (trusted / "guardian.py").write_text(g(d3, "show", "main:.guardian/guardian.py"))

        def ci(engine):
            return subprocess.run([sys.executable, str(engine), "check", "--base", "main"],
                                  cwd=d3, capture_output=True).returncode
        ok(ci(d3 / ".guardian/guardian.py") == 0, "sanity: PR engine is neutered")
        ok(ci(trusted / "guardian.py") == 1, "CI base engine blocks a PR that neuters guardian.py")

        # --- context
        ctx = run_context(d, "donations")[1]
        ok(ctx[:2] == ["module donations: src/donations/**", "depends [enforced DEPS]: users, reports"]
           and any(line.startswith("ARCH-01 [enforced]") for line in ctx), f"context module: {ctx}")
        ok(any("ARCH-01" in line for line in run_context(d, "src/donations/controllers/c.ts")[1])
           and not any("ARCH-01" in line for line in run_context(d, "src/donations/api.ts")[1]), "context path")
        many = "".join(f'[[rules]]\nid = "A{i}"\ntype = "advisory"\nmodules = ["users"]\nmsg = "{"x" * 80}"\n'
                       for i in range(40))
        put(d, TOML_REL, ST_TOML + many)
        ctx = run_context(d, "users")[1]
        ok(ctx[-1].startswith("(+") and sum(len(x) + 1 for x in ctx[:-1]) <= CONTEXT_BUDGET, "context budget")
        ok(raises(lambda: run_context(d, "nosuchmodule"), "G002"), "context unknown module")

        # --- exit-code contract: crash -> 2, hook mapping
        global run_check
        real = run_check
        try:
            def boom(*a, **k):
                raise RuntimeError("boom")
            run_check = boom
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                ok(main(["check"]) == 2, "internal crash -> exit 2")
                ok(main(["check", "--bogus"]) == 2, "bad argument -> exit 2")
                ok(run_hook(d, "{}") == 0, "hook: ERROR stops agent with exit 0 + continue:false")
            run_check = lambda *a, **k: (1, ["BLOCK X f"])
            with contextlib.redirect_stderr(io.StringIO()):
                ok(run_hook(d, "{}") == 2, "hook: BLOCK -> exit 2")
            with contextlib.redirect_stdout(io.StringIO()) as o:
                ok(run_hook(d, '{"stop_hook_active": true}') == 0 and "systemMessage" in o.getvalue(),
                   "hook: unresolved BLOCK on retry lets agent stop")
        finally:
            run_check = real
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


if __name__ == "__main__":
    sys.exit(main())
