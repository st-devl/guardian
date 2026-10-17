"""Guardian V2 uçtan uca testleri: python3 -m unittest discover -s tests -v"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUARDIAN = REPO / "guardian.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CODEX_HELP = "Usage: codex exec [OPTIONS] [PROMPT]\n  -s, --sandbox <MODE>\n  -c, --config <key=value>\n"


class E2E(unittest.TestCase):
    fixture = "laravel"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="guardian-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "proje"
        shutil.copytree(FIXTURES / self.fixture, self.root, ignore=shutil.ignore_patterns("_fake"))
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        for name, body in (("codex", f"#!/bin/sh\ncat <<'EOF'\n{CODEX_HELP}EOF\n"), ("claude", "#!/bin/sh\nexit 0\n")):
            (bin_ / name).write_text(body)
            (bin_ / name).chmod(0o755)
        gitcfg = self.tmp / "gitconfig"
        gitcfg.write_text("[user]\n\tname = test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
                          "[commit]\n\tgpgsign = false\n")
        self.log = self.tmp / "claude.log"
        self.env = {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
                    "GIT_CONFIG_GLOBAL": str(gitcfg), "GIT_CONFIG_NOSYSTEM": "1",
                    "GUARDIAN_CLAUDE_CMD": f"{sys.executable} {REPO / 'tests/fake_claude.py'}",
                    "FAKE_DIR": str(FIXTURES / self.fixture / "_fake"), "FAKE_LOG": str(self.log)}
        for k in ("GUARDIAN_GUNCELLEME", "FAKE_YARIDA", "CLAUDE_PROJECT_DIR"):
            self.env.pop(k, None)

    # ------------------------------------------------------------ yardımcılar
    def run_(self, *cmd, stdin="", env=None, check=None):
        p = subprocess.run(cmd, cwd=self.root, input=stdin, capture_output=True, text=True,
                           env={**self.env, **(env or {})})
        if check is not None:
            self.assertEqual(p.returncode, check, p.stdout + p.stderr)
        return p

    def guardian(self, stdin="e\n", **kw):
        return self.run_(sys.executable, str(GUARDIAN), stdin=stdin, **kw)

    def local(self, *args, **kw):
        return self.run_(sys.executable, ".guardian/guardian.py", *args, **kw)

    def git(self, *args, **kw):
        return self.run_("git", *args, **kw)

    def durum(self):
        return json.loads((self.root / ".guardian/durum.json").read_text())

    def install(self):
        p = self.guardian()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.durum()["asama"], "aktif")
        return p

    def commit_all(self, msg="iş", **kw):
        self.git("add", "-A")
        return self.git("commit", "-qm", msg, **kw)

    def edit(self, rel, fn):
        p = self.root / rel
        p.write_text(fn(p.read_text()))

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(self.root).parts[:1]
                and ".cache" not in p.parts}

    def hook(self):
        cmd = json.loads((self.root / ".claude/settings.json").read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
        return self.run_("sh", "-c", cmd, stdin="{}", env={"CLAUDE_PROJECT_DIR": str(self.root)})

    # ------------------------------------------------------------ 1
    def test_01_kurulum_tamamlanir(self):
        p = self.install()
        self.assertIn("doktor: 13/13 geçti", p.stdout)
        self.assertIn('Claude Code bu klasöre güvenmek isteyip istemediğinizi sorarsa "evet" deyin; yoksa izinler yüklenmez.',
                      p.stdout)
        self.assertEqual(self.log.read_text(), "KURULUM\n")
        self.assertEqual(self.git("config", "core.hooksPath").stdout.strip(), ".guardian/git-hooks")
        self.assertIn("- giris · html: TASARIM/giris/code.html", (self.root / ".guardian/tasarim.md").read_text())
        self.commit_all("kurulum", check=0)

    # ------------------------------------------------------------ 2
    def test_02_izinsiz_paket_hook_ve_commit_reddi(self):
        self.install()
        self.commit_all("kurulum", check=0)
        self.edit("composer.json", lambda t: t.replace('"laravel/framework"', '"guzzlehttp/guzzle": "^7", "laravel/framework"'))
        h = self.hook()
        self.assertEqual(h.returncode, 2)
        self.assertIn("guzzlehttp/guzzle", h.stderr)
        c = self.commit_all()
        self.assertNotEqual(c.returncode, 0)
        self.assertIn("guzzlehttp/guzzle", c.stdout + c.stderr)

    # ------------------------------------------------------------ 3
    def test_03_motor_dosyasi_commit_edilemez(self):
        self.install()
        self.commit_all("kurulum", check=0)
        self.edit(".guardian/guardian.py", lambda t: t + "\n# değişiklik\n")
        c = self.commit_all()
        self.assertNotEqual(c.returncode, 0)
        self.assertIn("motor güncellemesiyle", c.stdout + c.stderr)

    # ------------------------------------------------------------ 4
    def test_04_toml_degisikligi_karar_ister(self):
        self.install()
        self.commit_all("kurulum", check=0)
        self.edit(".guardian/guardian.toml", lambda t: t.replace('"pestphp/pest"]', '"pestphp/pest", "guzzlehttp/guzzle"]'))
        c = self.commit_all()
        self.assertNotEqual(c.returncode, 0)
        self.assertIn("KARARLAR.md", c.stdout + c.stderr)
        self.edit("KARARLAR.md", lambda t: t + "- K-002 · 2026-10-17 · Guzzle eklendi — neden: ödeme API'si\n")
        self.commit_all(check=0)

    # ------------------------------------------------------------ 5
    def test_05_agents_md_siniri(self):
        self.install()
        self.edit("AGENTS.md", lambda t: t + "".join(f"- ek kural {i}\n" for i in range(60)))
        p = self.local("kontrol")
        self.assertEqual(p.returncode, 1)
        self.assertIn("AGENTS.md:", p.stdout)

    # ------------------------------------------------------------ 6, 7, 8
    def test_06_hook_silinirse_doktor_kopugu_yazar(self):
        self.install()
        s = json.loads((self.root / ".claude/settings.json").read_text())
        del s["hooks"]["Stop"]
        (self.root / ".claude/settings.json").write_text(json.dumps(s))
        p = self.local("doktor")
        self.assertEqual(p.returncode, 1)
        self.assertIn("✗ claude-hook: .claude/settings.json: Stop hook'u", p.stdout)

    def test_07_hooks_path_bozulursa(self):
        self.install()
        self.git("config", "core.hooksPath", ".git/hooks")
        p = self.local("doktor")
        self.assertEqual(p.returncode, 1)
        self.assertIn("✗ git-hook", p.stdout)

    def test_08_yer_tutucu_kalirsa(self):
        self.install()
        self.edit("AGENTS.md", lambda t: t + "{{ILKELER}}\n")
        p = self.local("doktor")
        self.assertEqual(p.returncode, 1)
        self.assertIn("✗ yer-tutucu: AGENTS.md:", p.stdout)

    # ------------------------------------------------------------ 9
    def test_09_yarida_kalan_kurulum_devam_eder(self):
        p = self.guardian(env={"FAKE_YARIDA": "1"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Kurulum tamamlanmadı", p.stdout)
        self.assertNotEqual(self.durum()["asama"], "aktif")
        p = self.guardian(check=0)
        self.assertIn("kaldığı yerden devam", p.stdout)
        self.assertEqual(self.durum()["asama"], "aktif")
        self.assertEqual(self.log.read_text(), "KURULUM\nKURULUM\n")

    # ------------------------------------------------------------ 10
    def test_10_tekrar_calisma_idempotent(self):
        self.install()
        self.commit_all("kurulum", check=0)
        before = self.snapshot()
        p = self.guardian(check=0)
        self.assertEqual(p.stdout.strip(), "doktor: 13/13 geçti")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        self.assertEqual(self.log.read_text(), "KURULUM\n")

    # ------------------------------------------------------------ 11
    def test_11_mevcut_dosyalar_yedeklenir_ve_birlestirilir(self):
        (self.root / "CLAUDE.md").write_text("# benim eski talimatlarım\n")
        (self.root / ".claude").mkdir()
        (self.root / ".claude/settings.json").write_text(json.dumps(
            {"model": "opus", "permissions": {"allow": ["Bash(ls *)"]}}))
        p = self.install()
        self.assertIn("yedeklendi", p.stdout)
        backups = list((self.root / ".guardian/yedek").glob("*/CLAUDE.md"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "# benim eski talimatlarım\n")
        s = json.loads((self.root / ".claude/settings.json").read_text())
        self.assertEqual(s["model"], "opus")
        self.assertIn("Bash(ls *)", s["permissions"]["allow"])
        self.assertIn("Edit(/.guardian/guardian.py)", s["permissions"]["deny"])
        self.assertTrue((self.root / "CLAUDE.md").read_text().startswith("<!-- guardian:v2 -->"))

    # ------------------------------------------------------------ 12
    def test_12_proje_md_degisince_guncelleme(self):
        self.install()
        self.edit("proje.md", lambda t: t + "3. Raporlar ekranı.\n")
        (self.root / "TASARIM/rapor.html").write_text("<html></html>\n")
        p = self.guardian(check=0)
        self.assertIn("güncelleme modu", p.stdout)
        self.assertIn("Yeni ekran: rapor", p.stdout)
        self.assertEqual(self.log.read_text(), "KURULUM\nGÜNCELLEME\n")
        self.assertEqual((self.root / ".guardian/proje.son.md").read_text(), (self.root / "proje.md").read_text())
        self.assertIn("doktor: 13/13 geçti", p.stdout)

    # ------------------------------------------------------------ 13, 14
    def test_13_degismeyen_agac_onbellekten(self):
        self.install()
        sayac = self.tmp / "sayac"
        (self.root / "yeni.txt").write_text("x\n")
        env = {"GUARDIAN_TEST_SAYAC": str(sayac)}
        self.local("kontrol", env=env, check=0)
        self.local("kontrol", env=env, check=0)
        self.assertEqual(sayac.read_text(), "x")
        (self.root / "yeni.txt").write_text("y\n")
        self.local("kontrol", env=env, check=0)
        self.assertEqual(sayac.read_text(), "xx")

    def test_14_arac_yoksa_error(self):
        self.install()
        self.edit(".guardian/guardian.toml", lambda t: t.replace("python3 araclar/test.py", "olmayan-arac --test"))
        p = self.local("kontrol")
        self.assertEqual(p.returncode, 2)
        self.assertIn("ERROR G013", p.stdout)

    # ------------------------------------------------------------ ek
    def test_15_motor_guncelleme_statik_dosyalari_yeniler(self):
        self.install()
        self.commit_all("kurulum", check=0)
        d = self.durum()
        d["kit_surumu"] = "1.9.0"
        (self.root / ".guardian/durum.json").write_text(json.dumps(d))
        self.edit("CLAUDE.md", lambda t: t + "eski satır\n")
        self.commit_all("eski kit", check=0)
        p = self.guardian(check=0)
        self.assertIn("Motor güncellendi: 1.9.0 → 2.0.0", p.stdout)
        self.assertNotIn("eski satır", (self.root / "CLAUDE.md").read_text())
        self.assertIn("guardian: motor 2.0.0", self.git("log", "-1", "--format=%s").stdout)

    def test_16_proje_md_yoksa_error(self):
        (self.root / "proje.md").unlink()
        p = self.guardian()
        self.assertEqual(p.returncode, 2)
        self.assertIn("proje.md yok veya boş", p.stdout)
        self.assertFalse((self.root / ".guardian").exists())


class E2ENext(E2E):
    """Aynı sözleşme Next.js benzeri projede; [ithalat] etkin, origin GitHub (CI kurulur)."""
    fixture = "nextjs"

    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q"], cwd=self.root, env=self.env, check=True)
        subprocess.run(["git", "remote", "add", "origin", "git@github.com:ornek/vitrin.git"], cwd=self.root,
                       env=self.env, check=True)

    def test_01_kurulum_tamamlanir(self):
        self.install()
        self.assertTrue((self.root / ".github/workflows/guardian.yml").is_file())
        self.assertTrue(self.durum()["ci"])

    def test_ithalat_ihlali_block(self):
        self.install()
        (self.root / "src/lib/kotu.ts").write_text('import { Fiyat } from "../ui/Fiyat";\n')
        p = self.local("kontrol")
        self.assertEqual(p.returncode, 1)
        self.assertIn("src/lib/kotu.ts:1 -> src/ui/Fiyat lib -> ui", p.stdout)

    def test_02_izinsiz_paket_hook_ve_commit_reddi(self):
        self.install()
        self.commit_all("kurulum", check=0)
        self.edit("package.json", lambda t: t.replace('"next"', '"lodash": "4", "next"'))
        self.assertEqual(self.hook().returncode, 2)
        self.assertNotEqual(self.commit_all().returncode, 0)

    test_04_toml_degisikligi_karar_ister = test_05_agents_md_siniri = None
    test_03_motor_dosyasi_commit_edilemez = test_06_hook_silinirse_doktor_kopugu_yazar = None
    test_07_hooks_path_bozulursa = test_08_yer_tutucu_kalirsa = test_09_yarida_kalan_kurulum_devam_eder = None
    test_11_mevcut_dosyalar_yedeklenir_ve_birlestirilir = test_12_proje_md_degisince_guncelleme = None
    test_13_degismeyen_agac_onbellekten = test_14_arac_yoksa_error = None
    test_15_motor_guncelleme_statik_dosyalari_yeniler = test_16_proje_md_yoksa_error = None


if __name__ == "__main__":
    unittest.main()
