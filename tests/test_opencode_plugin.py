import os
import shutil
import subprocess
import unittest


def node_supports_type_stripping():
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return None
    major = int(out.lstrip("v").split(".")[0])
    minor = int(out.split(".")[1]) if "." in out else 0
    # --experimental-strip-types landed in node 22.6
    if (major, minor) >= (22, 6):
        return node
    return None


NODE = node_supports_type_stripping()


@unittest.skipUnless(NODE, "node >= 22.6 not available for TypeScript smoke test")
class TestOpenCodePlugin(unittest.TestCase):
    def test_plugin_smoke(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo_root, "tests", "opencode_smoke.ts")
        result = subprocess.run(
            [NODE, "--experimental-strip-types", "--no-warnings", script],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=60,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"smoke test failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("OK", result.stdout)


if __name__ == "__main__":
    unittest.main()
