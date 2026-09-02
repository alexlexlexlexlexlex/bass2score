"""
Spleeter's own model downloader (spleeter.model.provider.github) requires
httpx with HTTP/2 support and, in our testing, silently produced an empty
directory in some environments. This module downloads and extracts the
official pretrained model archives directly and deterministically.

Models are published as GitHub release assets of deezer/spleeter, which is
reachable from pretty much any network environment (unlike some ML model
hosts) since it's a plain GitHub release download.
"""
import os
import tarfile
import tempfile

import requests

RELEASE_BASE = "https://github.com/deezer/spleeter/releases/download/v1.4.0"


def ensure_model(model_name: str, models_root: str = "pretrained_models") -> str:
    """Ensure `<models_root>/<model_name>` contains the extracted model
    checkpoint, downloading it if necessary. Returns the model directory
    path (matching what spleeter.model.provider.ModelProvider expects)."""
    model_dir = os.path.join(models_root, model_name)
    checkpoint_path = os.path.join(model_dir, "checkpoint")
    if os.path.isfile(checkpoint_path):
        return model_dir

    os.makedirs(model_dir, exist_ok=True)
    url = f"{RELEASE_BASE}/{model_name}.tar.gz"
    print(f"Downloading model archive {url} ...", flush=True)

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=1 << 20):
                tmp.write(chunk)

    try:
        with tarfile.open(tmp_path) as tar:
            tar.extractall(model_dir)
    finally:
        os.remove(tmp_path)

    if not os.path.isfile(checkpoint_path):
        raise RuntimeError(
            f"Model download for {model_name!r} did not produce a checkpoint "
            f"file at {checkpoint_path!r}."
        )
    return model_dir
