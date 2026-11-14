"""Chart imports must work with a read-only application directory."""
import os
import subprocess
import sys


def test_chart_cache_uses_runtime_state(tmp_path):
    env = {**os.environ, "SONAR_STATE_DIR": str(tmp_path)}
    env.pop("MPLCONFIGDIR", None)
    result = subprocess.run(
        [sys.executable, "-c", "import settings; import matplotlib; print(matplotlib.get_configdir())"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == str(tmp_path / "cache" / "matplotlib")
    assert "temporary cache directory" not in result.stderr


def test_explicit_chart_cache_is_preserved(tmp_path):
    env = {**os.environ, "SONAR_STATE_DIR": str(tmp_path / "state"), "MPLCONFIGDIR": str(tmp_path / "custom")}
    result = subprocess.run(
        [sys.executable, "-c", "import settings; import matplotlib; print(matplotlib.get_configdir())"],
        env=env, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == str(tmp_path / "custom")
