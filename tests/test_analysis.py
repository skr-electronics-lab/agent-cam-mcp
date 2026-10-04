"""Unit tests for image analysis: brightness, contrast, SSIM, regions, and contact sheets."""

from __future__ import annotations

import numpy as np

from agent_cam.analysis.baseline import compute_ssim, generate_diff_heatmap
from agent_cam.analysis.led import analyze_led_signal, classify_led_color
from agent_cam.analysis.metrics import calculate_metrics
from agent_cam.analysis.regions import annotate_regions, crop_region, order_quad_points
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


def test_order_quad_points():
    pts = np.array([
        [10.0, 100.0],
        [120.0, 15.0],
        [15.0, 10.0],
        [110.0, 105.0]
    ], dtype="float32")

    rect = order_quad_points(pts)
    assert np.allclose(rect[0], [15.0, 10.0])
    assert np.allclose(rect[1], [120.0, 15.0])
    assert np.allclose(rect[2], [110.0, 105.0])
    assert np.allclose(rect[3], [10.0, 100.0])


def test_crop_region_quad_perspective():
    frame = np.zeros((400, 400, 3), dtype=np.uint8)
    frame[50:150, 50:150] = (0, 255, 0)

    quad_points = [
        [0.1, 0.1],
        [0.4, 0.12],
        [0.38, 0.42],
        [0.08, 0.4]
    ]

    reg = Region(
        name="angled_screen",
        x=0.08,
        y=0.1,
        w=0.32,
        h=0.32,
        units=RegionUnit.NORMALIZED,
        points=quad_points
    )

    warped, bounds = crop_region(frame, reg)
    assert warped is not None
    assert len(warped.shape) == 3
    assert bounds[0] == 32
    assert bounds[1] == 40
    assert bounds[2] == 160
    assert bounds[3] == 168
    assert warped.shape[0] > 10
    assert warped.shape[1] > 10


def test_annotate_regions_quad():
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    reg = Region(
        name="quad_panel",
        x=0.1,
        y=0.1,
        w=0.4,
        h=0.4,
        units=RegionUnit.NORMALIZED,
        points=[[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]]
    )

    annotated = annotate_regions(frame, [reg], highlight_name="quad_panel")
    assert annotated.shape == frame.shape
    assert np.any(annotated > 0)


def test_classify_led_color():
    assert classify_led_color([10, 10, 10]) == "off"
    assert classify_led_color([240, 240, 240]) == "white"
    assert classify_led_color([220, 20, 20]) == "red"
    assert classify_led_color([20, 230, 30]) == "green"
    assert classify_led_color([20, 30, 230]) == "blue"
    assert classify_led_color([240, 150, 20]) == "amber"


def test_analyze_led_blinking():
    fps = 20.0
    duration_s = 1.5
    num_frames = int(fps * duration_s)
    samples = []

    for i in range(num_frames):
        t = i / fps
        is_lit = (int(t * 4) % 2) == 0
        img = np.zeros((30, 30, 3), dtype=np.uint8)
        if is_lit:
            img[:, :] = (20, 220, 30)
        else:
            img[:, :] = (10, 20, 10)
        samples.append((t, img))

    res = analyze_led_signal(samples, camera_id="0", region_name="test_led")
    assert res.state == "blinking"
    assert res.color_name == "green"
    assert 1.0 <= res.frequency_hz <= 3.0
    assert 30.0 <= res.duty_cycle <= 70.0
    assert res.sample_count == num_frames


def test_analyze_led_solid_on():
    samples = []
    for i in range(20):
        t = i / 20.0
        img = np.zeros((30, 30, 3), dtype=np.uint8)
        img[:, :] = (15, 15, 230)
        samples.append((t, img))

    res = analyze_led_signal(samples, camera_id="0", region_name="power_led")
    assert res.state == "solid_on"
    assert res.color_name == "red"
    assert res.duty_cycle == 100.0


def test_analyze_led_off():
    samples = []
    for i in range(15):
        t = i / 15.0
        img = np.zeros((20, 20, 3), dtype=np.uint8)
        samples.append((t, img))

    res = analyze_led_signal(samples, camera_id="0", region_name="dark_led")
    assert res.state == "off"
    assert res.color_name == "off"

