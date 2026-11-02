"""Publication must not accept skipped uploads with different existing bytes."""

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_pypi", ROOT / "scripts/verify-pypi.py")
assert spec and spec.loader
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def fixture(tmp_path):
    files = {"dine_mcp-1.2.3-py3-none-any.whl": b"synthetic-wheel", "dine_mcp-1.2.3.tar.gz": b"synthetic-source"}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    expected = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    metadata = {
        "info": {"name": "dine-mcp", "version": "1.2.3"},
        "urls": [
            {"filename": name, "digests": {"sha256": digest}, "yanked": False} for name, digest in expected.items()
        ],
    }
    return expected, metadata


def test_exact_tested_distributions_are_required(tmp_path):
    expected, metadata = fixture(tmp_path)
    assert publisher.distribution_hashes(tmp_path, "1.2.3") == expected
    publisher.verify(metadata, "1.2.3", expected)
    for version in ("../other", "v1.2.3", "1.2.3/extra"):
        with pytest.raises(ValueError):
            publisher.distribution_hashes(tmp_path, version)
    (tmp_path / "other.whl").write_bytes(b"unexpected")
    with pytest.raises(ValueError):
        publisher.distribution_hashes(tmp_path, "1.2.3")


@pytest.mark.parametrize(
    "change",
    [
        "version",
        "package",
        "missing",
        "duplicate",
        "extra_wheel",
        "bad_row",
        "bad_filename",
        "yanked",
        "hash",
        "malformed",
    ],
)
def test_drifted_pypi_metadata_refuses(change, tmp_path):
    expected, original = fixture(tmp_path)
    metadata = deepcopy(original)
    if change == "version":
        metadata["info"]["version"] = "1.2.4"
    elif change == "package":
        metadata["info"]["name"] = "other-package"
    elif change == "missing":
        metadata["urls"].pop()
    elif change == "duplicate":
        metadata["urls"].append(deepcopy(metadata["urls"][0]))
    elif change == "extra_wheel":
        extra = deepcopy(metadata["urls"][0])
        extra["filename"] = "dine_mcp-1.2.3-cp312-cp312-manylinux_2_28_x86_64.whl"
        metadata["urls"].append(extra)
    elif change == "bad_row":
        metadata["urls"][0] = None
    elif change == "bad_filename":
        metadata["urls"][0]["filename"] = []
    elif change == "yanked":
        metadata["urls"][0]["yanked"] = True
    elif change == "hash":
        metadata["urls"][0]["digests"]["sha256"] = "0" * 64
    else:
        metadata["urls"][0]["digests"] = None
    with pytest.raises(ValueError):
        publisher.verify(metadata, "1.2.3", expected)
