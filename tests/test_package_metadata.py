"""Published distributions must retain security floors used by locked installs."""

import subprocess
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def test_distributions_publish_the_security_constraints(tmp_path):
    subprocess.run(
        ["uv", "build", "--offline", "--no-sources", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    constraints = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["constraint-dependencies"]
    expected = {Requirement(item) for item in constraints}
    assert expected
    with zipfile.ZipFile(next(tmp_path.glob("*.whl"))) as wheel:
        wheel_metadata = wheel.read(next(name for name in wheel.namelist() if name.endswith("/METADATA")))
    with tarfile.open(next(tmp_path.glob("*.tar.gz"))) as source:
        metadata = source.extractfile(next(item for item in source.getmembers() if item.name.endswith("/PKG-INFO")))
        assert metadata is not None
        source_metadata = metadata.read()
    for raw in (wheel_metadata, source_metadata):
        published = Parser().parsestr(raw.decode()).get_all("Requires-Dist", [])
        assert expected <= {Requirement(item) for item in published}
