"""Analysis package for Agent Cam MCP."""

from agent_cam.analysis.baseline import BaselineManager, compute_ssim, generate_diff_heatmap
from agent_cam.analysis.metrics import calculate_metrics
from agent_cam.analysis.ocr import OCRError, perform_ocr
from agent_cam.analysis.regions import annotate_regions, crop_region
from agent_cam.analysis.sequence import create_contact_sheet
from agent_cam.analysis.watcher import watch_condition

__all__ = [
    "BaselineManager",
    "compute_ssim",
    "generate_diff_heatmap",
    "calculate_metrics",
    "OCRError",
    "perform_ocr",
    "annotate_regions",
    "crop_region",
    "create_contact_sheet",
    "watch_condition",
]
