"""Optical character recognition (OCR) with optional extra detection."""

from __future__ import annotations

import logging
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class OCRError(Exception):
    def __init__(self, message: str, is_not_installed: bool = False):
        super().__init__(message)
        self.is_not_installed = is_not_installed


def perform_ocr(frame: np.ndarray) -> Tuple[str, float]:
    """Perform OCR on an image slice. Returns (extracted_text, average_confidence).

    Raises OCRError(is_not_installed=True) if neither rapidocr nor pytesseract is present.
    """
    # 1. Try RapidOCR
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
        result, _ = engine(frame)
        if not result:
            return "", 1.0

        texts = []
        confidences = []
        for item in result:
            # item shape: [box, text, score]
            if len(item) >= 3:
                texts.append(str(item[1]))
                confidences.append(float(item[2]))

        joined_text = "\n".join(texts)
        avg_conf = float(sum(confidences) / len(confidences)) if confidences else 1.0
        return joined_text, round(avg_conf, 3)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("RapidOCR failed: %s", e)

    # 2. Try pytesseract
    try:
        import pytesseract

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT)
        texts = []
        confs = []
        for i, text in enumerate(data.get("text", [])):
            if text.strip():
                texts.append(text.strip())
                try:
                    c = float(data["conf"][i])
                    if c >= 0:
                        confs.append(c / 100.0)
                except Exception:
                    pass
        joined = " ".join(texts)
        avg_conf = float(sum(confs) / len(confs)) if confs else 0.8
        return joined, round(avg_conf, 3)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("Pytesseract failed: %s", e)

    raise OCRError(
        "OCR extra is not installed. Install with: pip install 'agent-cam-mcp[ocr]'",
        is_not_installed=True,
    )
