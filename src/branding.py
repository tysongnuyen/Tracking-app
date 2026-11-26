"""Load the Tracking wordmark and icon from source or a frozen app bundle."""

from functools import lru_cache
from pathlib import Path
import sys

from PIL import Image


def asset_path(name):
    """Return a bundled branding asset without relying on the current directory."""
    if Path(name).name != name:
        raise ValueError("Branding asset names must be plain filenames")
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


@lru_cache(maxsize=2)
def _load(name):
    with Image.open(asset_path(name)) as image:
        return image.convert("RGBA")


def _fit(image, size):
    width, height = (max(1, int(value)) for value in size)
    copy = image.copy()
    copy.thumbnail((width, height), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    result.alpha_composite(copy, ((width - copy.width) // 2, (height - copy.height) // 2))
    return result


def wordmark_image(size=(150, 58)):
    """Return a fitted copy of the white handwritten Tracking wordmark."""
    return _fit(_load("tracking-wordmark.png"), size)


def app_icon(size=64):
    """Return a square copy of the T icon for a window or the Windows tray."""
    # The ICO holds a maximum 256px runtime image, so the background collector
    # does not retain the large original generated PNG just to show a tray icon.
    return _fit(_load("tracking.ico"), (size, size))
