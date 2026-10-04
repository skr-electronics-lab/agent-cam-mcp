# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-04

### Added
- Initial release of Agent Cam MCP Tool.
- Daemon architecture with single camera ownership, DirectShow, V4L2, and AVFoundation OS backends.
- RAM ring buffer for historical timeline frames with low-fps JPEG compression and bounded memory caps.
- Background grabber threads delivering memory-based sub-5ms image capture.
- Complete suite of 17 MCP tools conforming to the specification:
  - `get_status`
  - `list_cameras`
  - `capture_image`
  - `capture_sequence`
  - `get_timeline`
  - `measure`
  - `watch`
  - `define_region`
  - `list_regions`
  - `delete_region`
  - `request_region_from_user`
  - `save_baseline`
  - `compare_to_baseline`
  - `read_text`
  - `set_camera_settings`
  - `read_events`
  - `run_action`
- Quantitative measurements: mean brightness, contrast, lit pixel %, dominant colors, edge density, sharpness (Laplacian variance), and motion level.
- SSIM baseline comparison and difference heatmaps.
- Hardware adapters for serial COM ports, logfile tailing, and MQTT topic subscriptions.
- Safe allowlisted action runner with timeouts, argv execution, and no shell evaluation.
- Local instrument dashboard served via FastAPI and WebSockets with dark and light themes.
- Interactive canvas for defining regions of interest and permanent black privacy masks.
- Self-diagnosis `doctor` command and API endpoint testing 11 system and hardware checks.
- Client registration command supporting Antigravity, Claude Code, Cursor, and Codex with safe JSON merging and automated backups.
