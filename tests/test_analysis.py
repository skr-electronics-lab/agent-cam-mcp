"""Unit tests for image analysis: brightness, contrast, SSIM, regions, and contact sheets."""

from __future__ import annotations

import numpy as np

from agent_cam.analysis.baseline import compute_ssim, generate_diff_heatmap
from agent_cam.analysis.metrics import calculate_metrics
from agent_cam.analysis.regions import annotate_regions, crop_region
from agent_cam.analysis.sequence import create_contact_sheet
from agent_cam.models import Region, RegionUnit


def test_calculate_metrics_black_and_white():
    # Pitch black frame
    black = np.zeros((100, 100, 3), dtype=np.uint8)
    res_b = calculate_metrics(black)
    assert res_b.mean_brightness == 0.0
    assert res_b.contrast == 0.0
    assert res_b.lit_pixel_percentage == 0.0

    # Pure white frame
    white = np.full((100, 100, 3), 255, dtype=np.uint8)
    res_w = calculate_metrics(white)
    assert res_w.mean_brightness == 255.0
    assert res_w.contrast == 0.0
    assert res_w.lit_pixel_percentage == 100.0


def test_calculate_metrics_motion():
    f1 = np.zeros((100, 100, 3), dtype=np.uint8)
    f2 = np.full((100, 100, 3), 255, dtype=np.uint8)
    res = calculate_metrics(f2, previous_frame=f1)
    assert res.motion_level == 1.0


def test_ssim_identical_and_different():
    f1 = np.full((80, 80), 128, dtype=np.uint8)
    # Identical
    score_identical = compute_ssim(f1, f1)
    assert score_identical >= 0.99

    # Different
    f2 = np.full((80, 80), 0, dtype=np.uint8)
    score_diff = compute_ssim(f1, f2)
    assert score_diff < 0.5


def test_diff_heatmap():
    f1 = np.zeros((100, 100, 3), dtype=np.uint8)
    f2 = np.zeros((100, 100, 3), dtype=np.uint8)
    # Change half the frame
    f2[:, 50:] = 255
    heatmap, pct = generate_diff_heatmap(f2, f1)
    assert heatmap.shape == (100, 100, 3)
    assert 45.0 <= pct <= 55.0


def test_crop_region_normalized_and_pixels():
    frame = np.zeros((200, 300, 3), dtype=np.uint8)

    # Normalized
    reg_norm = Region(name="box1", x=0.1, y=0.1, w=0.5, h=0.5, units=RegionUnit.NORMALIZED)
    cropped_n, bounds_n = crop_region(frame, reg_norm)
    assert bounds_n == (30, 20, 180, 120)
    assert cropped_n.shape == (100, 150, 3)

    # Pixels
    reg_pix = Region(name="box2", x=10, y=20, w=100, h=50, units=RegionUnit.PIXELS)
    cropped_p, bounds_p = crop_region(frame, reg_pix)
    assert bounds_p == (10, 20, 110, 70)
    assert cropped_p.shape == (50, 100, 3)


def test_annotate_regions():
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    reg = Region(name="test_target", x=0.2, y=0.2, w=0.4, h=0.4)
    annotated = annotate_regions(frame, [reg], highlight_name="test_target")
    # Assert frame was drawn on (pixels changed from 0)
    assert np.count_nonzero(annotated) > 0


def test_create_contact_sheet():
    frames = [
        (np.full((100, 100, 3), 50, dtype=np.uint8), 1.0),
        (np.full((100, 100, 3), 100, dtype=np.uint8), 2.0),
        (np.full((100, 100, 3), 150, dtype=np.uint8), 3.0),
        (np.full((100, 100, 3), 200, dtype=np.uint8), 4.0),
    ]
    sheet = create_contact_sheet(frames, max_sheet_width=800)
    assert sheet is not None
    assert sheet.shape[0] > 100
    assert sheet.shape[1] > 100
