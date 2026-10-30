"""Fetch pinned Photoshop masters and verify before publishing to the cache."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
from urllib.request import Request, urlopen

BASE_URL = "https://github.com/GalgameCriticism/Autoweekly-skills/releases/download/templates-v1/"
ASSETS = {
    "weekly-cover-t1-16x9.psd": (47344037, "0ca7823eda963b4738f2d1e2b796139cdb7fd4b83f4cfb5566a39aa7997a6472", (2400, 1500)),
    "weekly-cover-t2-banner.psd": (80531013, "01210f74b049244a8be28a49d7d3c3bcc50efd56319ae984772c75c115456551", (3525, 1500)),
    "weekly-cover-t3-4x3.psd": (94403793, "01d093192265e9fd9a22c27943064d52319f7248cc2202827eb9bd35b1f7f996", (2400, 1800)),
    "weekly-bgm-overlay-master.psd": (46847955, "abdd472a0b266611273abc1d66559c62727565c7f6f74a8336636405dc3a4c7b", (2160, 1080)),
}


def cache_dir():
    configured = os.environ.get("AUTOWEEKLY_TEMPLATE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home() / ".cache"
    return base / "Autoweekly" / "templates" / "templates-v1"


def template_path(variable, legacy, filename):
    override = os.environ.get(variable)
    if override:
        return str(Path(override).expanduser())
    return str(Path(legacy) if Path(legacy).is_file() else cache_dir() / filename)


def verify(path, name):
    expected_bytes, expected_hash, expected_size = ASSETS[name]
    if path.stat().st_size != expected_bytes:
        raise ValueError(f"Wrong file size: {path}")
    with path.open("rb") as stream:
        header = stream.read(26)
        stream.seek(0)
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected_hash:
        raise ValueError(f"SHA256 mismatch: {path}; keep existing file and choose another --directory")
    if len(header) != 26 or header[:6] != b"8BPS\x00\x01":
        raise ValueError(f"Not a PSD: {path}")
    height, width = struct.unpack(">II", header[14:22])
    if (width, height) != expected_size:
        raise ValueError(f"Wrong PSD canvas: {path}")
    return {"path": str(path), "sha256": digest, "size": [width, height]}


def fetch(name, directory):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    if target.exists():
        return dict(verify(target, name), status="cached")
    expected_bytes = ASSETS[name][0]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            request = Request(BASE_URL + name, headers={"User-Agent": "Autoweekly-Skills/1"})
            with urlopen(request, timeout=60) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > expected_bytes:
                        raise ValueError(f"Download exceeds expected size: {name}")
                    output.write(chunk)
        verify(temporary, name)
        # Linking publishes a complete file atomically without replacing an existing master.
        try:
            os.link(temporary, target)
        except FileExistsError:
            return dict(verify(target, name), status="cached")
        return dict(verify(target, name), status="downloaded")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(group):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=cache_dir())
    args = parser.parse_args()
    names = [name for name in ASSETS if name.startswith("weekly-cover-" if group == "cover" else "weekly-bgm-")]
    try:
        records = []
        for name in names:
            print(f"Checking/downloading {name}", file=sys.stderr, flush=True)
            records.append(fetch(name, args.directory))
        print(json.dumps({"status": "ready", "directory": str(args.directory.resolve()), "templates": records}, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        return 1
