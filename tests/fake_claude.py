#!/usr/bin/env python3
"""Testlerde gerçek Claude Code'un yerine geçen sahte ajan.

Başlangıç mesajı son argümandır. Ortam: FAKE_DIR (fixture'ın _fake klasörü), FAKE_LOG
(çağrı günlüğü, depo dışında), FAKE_YARIDA=1 (kurulumun ortasında çık).
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

prompt = sys.argv[-1]
mode = next((m for m in ("KURULUM", "GÜNCELLEME", "İNCELEME") if f"# GUARDIAN {m}" in prompt
             or (m == "İNCELEME" and "AŞAMA SONU" in prompt)), "?")
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
    f.write(mode + "\n")


def fill(path, text):
    p = Path(path)
    p.write_text(re.sub(r"\{\{[^}]*\}\}", text, p.read_text(encoding="utf-8")), encoding="utf-8")


if mode == "KURULUM":
    fill("AGENTS.md", "- (sahte) proje.md'den çıkarıldı")
    if os.environ.get("FAKE_YARIDA"):
        sys.exit(1)  # DURUM.md ve guardian.toml yer tutucuları kalır
    shutil.copyfile(Path(os.environ["FAKE_DIR"]) / "guardian.toml", ".guardian/guardian.toml")
    fill("DURUM.md", "- [ ] G01 · Giriş ekranı")
    with open("KARARLAR.md", "a", encoding="utf-8") as f:
        f.write("- K-001 · 2026-10-17 · Test veritabanı SQLite — neden: hızlı testler\n")
    sys.exit(subprocess.run([sys.executable, ".guardian/guardian.py", "doktor"]).returncode)
if mode == "GÜNCELLEME":
    with open("DURUM.md", "a", encoding="utf-8") as f:
        f.write("- [ ] G02 · proje.md güncellemesinden gelen görev\n")
