import gzip
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Chạy trong tiến trình con: configure() thay sys.stdout/sys.stderr, không được ảnh hưởng tiến trình chạy test.
SCRIPT = textwrap.dedent("""
    import logging, warnings
    from src.core import logging_setup
    logging_setup.configure()
    logging.getLogger("demo").info("thong tin %s", 1)
    logging.getLogger("demo").warning("canh bao")
    logging.getLogger("httpx").info("GET https://x/?access_token=bi-mat")
    logging.getLogger("uvicorn.access").info('127.0.0.1 - "GET / HTTP/1.1" 200')
    logging.getLogger("uvicorn.error").info("Started server process")
    print("dong print")
    warnings.warn("canh bao thu vien")
    for i in range(1000):
        logging.getLogger("demo").info("x" * 3000)
""")


class LoggingSetupTest(unittest.TestCase):
    def test_logs_go_to_rotating_files_not_console(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "LOG_DIR": directory, "LOG_MAX_MB": "1", "LOG_BACKUPS": "2", "LOG_CONSOLE": "false",
                   "LOG_LEVEL": "INFO", "LOG_ACCESS": "true"}
            result = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, text=True,
                                    timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "")
            self.assertEqual(result.stderr.strip(), "")
            logs = Path(directory)
            app = (logs / "app.log").read_text(encoding="utf-8")
            rotated = sorted(path.name for path in logs.iterdir() if path.name.startswith("app.log."))
            self.assertEqual(rotated, ["app.log.1.gz", "app.log.2.gz"])        # xoay vòng + nén, giữ 2 bản
            everything = app + "".join(gzip.open(logs / name, "rt", encoding="utf-8").read() for name in rotated)
            for text in ("dong print", "canh bao thu vien", "Started server process"):
                self.assertIn(text, everything)
            self.assertNotIn("bi-mat", everything)                            # httpx chỉ giữ cảnh báo
            self.assertNotIn("GET / HTTP", everything)                        # access log tách riêng
            self.assertIn("GET / HTTP", (logs / "access.log").read_text(encoding="utf-8"))
            errors = (logs / "error.log").read_text(encoding="utf-8")
            self.assertIn("canh bao", errors)
            self.assertNotIn("thong tin", errors)


if __name__ == "__main__":
    unittest.main()
