"""Baseline image storage, SSIM comparison, and diff heatmap generation."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.config import get_baselines_dir
from agent_cam.models import BaselineInfo, ComparisonResult

logger = logging.getLogger(__name__)


def compute_ssim(img1: np.ndarray, img2: np.ndarray) -> float:
    """Compute structural similarity index (SSIM) between two grayscale images [0.0 - 1.0]."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    # Convert to float
    i1 = img1.astype(np.float64)
    i2 = img2.astype(np.float64)

    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    # Gaussian kernel
    kernel_size = 11
    sigma = 1.5

    mu1 = cv2.GaussianBlur(i1, (kernel_size, kernel_size), sigma)
    mu2 = cv2.GaussianBlur(i2, (kernel_size, kernel_size), sigma)

    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = cv2.GaussianBlur(i1 * i1, (kernel_size, kernel_size), sigma) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(i2 * i2, (kernel_size, kernel_size), sigma) - mu2_sq
    sigma12 = cv2.GaussianBlur(i1 * i2, (kernel_size, kernel_size), sigma) - mu1_mu2

    ssim_map = ((2 * mu1_mu2 + c1) * (2 * sigma12 + c2)) / (
        (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    )
    score = float(np.mean(ssim_map))
    return max(0.0, min(1.0, score))


def generate_diff_heatmap(current: np.ndarray, baseline: np.ndarray) -> Tuple[np.ndarray, float]:
    """Generate color diff heatmap overlaid on current image, and compute % changed pixels."""
    h, w = current.shape[:2]
    if baseline.shape[:2] != (h, w):
        base_resized = cv2.resize(baseline, (w, h))
    else:
        base_resized = baseline

    curr_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY) if len(current.shape) == 3 else current
    base_gray = (
        cv2.cvtColor(base_resized, cv2.COLOR_BGR2GRAY)
        if len(base_resized.shape) == 3
        else base_resized
    )

    diff = cv2.absdiff(curr_gray, base_gray)

    # Threshold for noticeable change
    change_threshold = 25
    changed_mask = diff > change_threshold
    changed_count = int(np.count_nonzero(changed_mask))
    total_pixels = max(1, h * w)
    pct_changed = float((changed_count / total_pixels) * 100.0)

    # Normalize diff for heatmap
    diff_norm = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX)  # type: ignore
    heatmap = cv2.applyColorMap(diff_norm, cv2.COLORMAP_JET)

    # Blend heatmap onto current image
    if len(current.shape) == 3:
        blended = cv2.addWeighted(current, 0.65, heatmap, 0.35, 0)
    else:
        curr_bgr = cv2.cvtColor(current, cv2.COLOR_GRAY2BGR)
        blended = cv2.addWeighted(curr_bgr, 0.65, heatmap, 0.35, 0)

    return blended, pct_changed


class BaselineManager:
    """Stores and compares baseline reference images."""

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = storage_dir or get_baselines_dir()

    def _paths_for_name(self, name: str) -> Tuple[Path, Path]:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_")).strip()
        img_path = self.storage_dir / f"{safe_name}.png"
        meta_path = self.storage_dir / f"{safe_name}.json"
        return img_path, meta_path

    def save_baseline(
        self,
        name: str,
        frame: np.ndarray,
        camera_id: str,
        region: Optional[str] = None,
    ) -> BaselineInfo:
        """Persist a reference image to the data directory."""
        img_path, meta_path = self._paths_for_name(name)
        cv2.imwrite(str(img_path), frame)

        h, w = frame.shape[:2]
        now = time.time()
        info = BaselineInfo(
            name=name,
            camera=camera_id,
            region=region,
            created_at=now,
            width=w,
            height=h,
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(info.model_dump(), f, indent=2)

        return info

    def get_baseline(self, name: str) -> Optional[Tuple[np.ndarray, BaselineInfo]]:
        """Load baseline frame and metadata by name."""
        img_path, meta_path = self._paths_for_name(name)
        if not img_path.exists() or not meta_path.exists():
            return None
        frame = cv2.imread(str(img_path))
        if frame is None:
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            info = BaselineInfo.model_validate(data)
            return frame, info
        except Exception:
            return None

    def list_baselines(self) -> List[BaselineInfo]:
        """List all saved baselines."""
        res: List[BaselineInfo] = []
        for meta_file in self.storage_dir.glob("*.json"):
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                res.append(BaselineInfo.model_validate(data))
            except Exception:
                pass
        return res

    def delete_baseline(self, name: str) -> bool:
        """Delete baseline files."""
        img_path, meta_path = self._paths_for_name(name)
        deleted = False
        if img_path.exists():
            img_path.unlink()
            deleted = True
        if meta_path.exists():
            meta_path.unlink()
            deleted = True
        return deleted

    def compare(
        self,
        name: str,
        current_frame: np.ndarray,
        threshold_similarity: float = 0.90,
    ) -> Tuple[Optional[ComparisonResult], Optional[np.ndarray]]:
        """Compare current frame to baseline. Returns (result, diff_heatmap)."""
        base_data = self.get_baseline(name)
        if base_data is None:
            return None, None
        base_frame, _ = base_data

        curr_gray = (
            cv2.cvtColor(current_frame, cv2.COLOR_BGR2GRAY)
            if len(current_frame.shape) == 3
            else current_frame
        )
        base_gray = (
            cv2.cvtColor(base_frame, cv2.COLOR_BGR2GRAY)
            if len(base_frame.shape) == 3
            else base_frame
        )

        ssim_score = compute_ssim(curr_gray, base_gray)
        diff_image, pct_changed = generate_diff_heatmap(current_frame, base_frame)

        result = ComparisonResult(
            name=name,
            similarity_score=round(ssim_score, 4),
            percent_changed_pixels=round(pct_changed, 2),
            is_match=ssim_score >= threshold_similarity,
            threshold=threshold_similarity,
            timestamp=time.time(),
        )
        return result, diff_image
