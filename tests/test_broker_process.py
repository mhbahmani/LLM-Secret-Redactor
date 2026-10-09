import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROKER = os.path.join(REPO_ROOT, "src", "secret_redactor", "broker.py")
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from secret_redactor import vault


class TestBrokerProcess(unittest.TestCase):
    def setUp(self):
        self.runtime_dir = tempfile.mkdtemp(prefix="llm-redactor-process-")
        self.env = {**os.environ, "SECRET_REDACTOR_RUNTIME_DIR": self.runtime_dir,
                    "SECRET_REDACTOR_BROKER_IDLE": "30"}
        self.previous_runtime_dir = os.environ.get("SECRET_REDACTOR_RUNTIME_DIR")
        os.environ["SECRET_REDACTOR_RUNTIME_DIR"] = self.runtime_dir
        self.processes = []

    def tearDown(self):
        vault.shutdown_broker()
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait()
        shutil.rmtree(self.runtime_dir, ignore_errors=True)
        if self.previous_runtime_dir is None:
            os.environ.pop("SECRET_REDACTOR_RUNTIME_DIR", None)
        else:
            os.environ["SECRET_REDACTOR_RUNTIME_DIR"] = self.previous_runtime_dir

    def start_broker(self):
        process = subprocess.Popen([sys.executable, BROKER, "--serve"], env=self.env)
        self.processes.append(process)
        return process

    def stats(self):
        # Talk to the socket directly; the client helpers would start a broker.
        return vault._send({"operation": "stats"})

    def wait_for_socket(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                return self.stats()["pid"]
            except OSError:
                time.sleep(0.02)
        self.fail("broker did not start")

    def test_concurrent_starts_leave_one_broker(self):
        brokers = [self.start_broker() for _ in range(10)]
        owner = self.wait_for_socket()
        time.sleep(0.5)
        running = [process.pid for process in brokers if process.poll() is None]
        self.assertEqual(running, [owner])

    def test_busy_broker_is_not_replaced(self):
        first = self.start_broker()
        self.wait_for_socket()
        vault.mask_text("DB_PASSWORD=hunter2hunter2", "busy-session")

        first.send_signal(signal.SIGSTOP)
        try:
            second = self.start_broker()
            second.wait(timeout=3)
        finally:
            first.send_signal(signal.SIGCONT)

        self.assertEqual(second.returncode, 0)
        stats = self.stats()
        self.assertEqual(stats["pid"], first.pid)
        self.assertEqual(stats["sessions"], 1)


if __name__ == "__main__":
    unittest.main()
