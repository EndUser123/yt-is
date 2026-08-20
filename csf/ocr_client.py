"""EasyOCR wrapper for capturing code on screen from video frames.

Non-fatal: timeouts and exceptions return empty list so the orchestrator
continues with partial results.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import re

# Boilerplate patterns to filter out
_BOILERPLATE_PATTERNS: list[re.Pattern[str]] = [
    # Navigation UI
    re.compile(r"^Subscribe$", re.IGNORECASE),
    re.compile(r"^Like$", re.IGNORECASE),
    re.compile(r"^Share$", re.IGNORECASE),
    re.compile(r"^Comment$", re.IGNORECASE),
    re.compile(r"^Home$", re.IGNORECASE),
    re.compile(r"^Videos$", re.IGNORECASE),
    re.compile(r"^Playlists$", re.IGNORECASE),
    re.compile(r"^Subscribe$", re.IGNORECASE),
    # Short numbers or symbols
    re.compile(r"^\d+$"),
    re.compile(r"^[^\w\s]+$"),
]

# Very short text (less than 3 chars after stripping whitespace)
_SHORT_TEXT_PATTERN = re.compile(r"^\s*.{0,2}\s*$")


def _is_boilerplate(text: str) -> bool:
    """Return True if text matches boilerplate patterns."""
    stripped = text.strip()
    if len(stripped) < 3:
        return True
    if _SHORT_TEXT_PATTERN.match(stripped):
        return True
    for pattern in _BOILERPLATE_PATTERNS:
        if pattern.match(stripped):
            return True
    return False


# Singleton reader — loaded once at module level
_reader: Optional["easyocr.Reader"] = None
_gpu_available: Optional[bool] = None


def _detect_gpu() -> bool:
    """Detect GPU availability for EasyOCR (cached after first call)."""
    global _gpu_available
    if _gpu_available is None:
        raw = os.environ.get("YTIS_OCR_GPU", "auto")
        if raw == "auto":
            try:
                import torch
                _gpu_available = torch.cuda.is_available()
            except ImportError:
                _gpu_available = False
        else:
            _gpu_available = raw == "1"
    return _gpu_available


def _get_reader() -> "easyocr.Reader":
    """Lazily create and return the EasyOCR singleton reader.

    GPU-first (review F-4, 2026-08-19): the RTX 5070 is idle while pass-1
    OCR runs up to 240 frames/video on CPU. Env override for CPU-only hosts.
    """
    global _reader
    if _reader is None:
        import easyocr

        _reader = easyocr.Reader(["en"], gpu=_detect_gpu(), verbose=False)
    return _reader


def _ocr_on_image(image_path: Path) -> list[str]:
    """Run OCR on a single image and return raw text results."""
    reader = _get_reader()
    results = reader.readtext(str(image_path))
    return [item[1] for item in results if item[1].strip()]


def extract_code_snippets(
    image_paths: list[Path], timeout_per_image: float = 30.0
) -> list[str]:
    """Run EasyOCR over a list of frame images and extract text.

    Args:
        image_paths: List of paths to frame image files.
        timeout_per_image: Seconds to wait per image before cancelling.

    Returns:
        List of non-boilerplate strings captured from frames.
        Returns [] if any image times out or raises an exception.
    """
    if not image_paths:
        return []

    all_snippets: list[str] = []

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_path = {
                executor.submit(_ocr_on_image, path): path for path in image_paths
            }

            for future in future_to_path:
                path = future_to_path[future]
                try:
                    snippets = future.result(timeout=timeout_per_image)
                    for snippet in snippets:
                        if not _is_boilerplate(snippet):
                            all_snippets.append(snippet.strip())
                except TimeoutError:
                    # Per-image timeout — cancel and continue
                    future.cancel()
                    continue
                except Exception:
                    # Non-fatal: continue with remaining images
                    continue
    except Exception:
        # Non-fatal at the executor level too
        return []

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for s in all_snippets:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    return deduped


def extract_text_per_frame(image_paths: list[Path], *, max_workers: int = 4) -> list[str]:
    """OCR each frame independently; results aligned by input index.

    Unlike extract_code_snippets (deduped, order-free), this preserves the
    frame alignment the visual pipeline needs to pick code-dense timestamps
    for native-resolution re-capture. A failed frame yields "" rather than
    aborting the batch; a reader-level failure yields ["", ...] alignment.
    """
    if not image_paths:
        return []
    paths = [Path(p) for p in image_paths]
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_ocr_on_image, path) for path in paths]
            per_frame_lines: list[list[str]] = []
            for future in futures:
                try:
                    per_frame_lines.append(future.result(timeout=120.0))
                except Exception:
                    per_frame_lines.append([])
    except Exception:
        return ["" for _ in paths]
    return [
        "\n".join(line for line in lines if line.strip()) for lines in per_frame_lines
    ]


def shutdown() -> None:
    """Release the EasyOCR reader and free GPU memory."""
    global _reader, _gpu_available
    _reader = None
    _gpu_available = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass
