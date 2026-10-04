# Agent Cam MCP Tool - Agent Integration Guide

> Agent Cam MCP Tool gives AI agents real physical perception through webcams and IP camera streams.
> It provides direct image capture, quantitative measurements, and watch loops without saving files to disk.

## When to Call Tools

- Call `get_status` at the beginning of tasks to verify camera operational status and access permissions.
- Call `list_cameras` to inspect available physical USB webcams and network streams.
- Call `capture_image` whenever you need to see physical hardware state (after builds, during debugging, while observing motors or displays).
- Call `measure` on the frame or defined regions to get deterministic numerical metrics (brightness, contrast, lit pixel %, edge density, sharpness, motion) without transmitting large image tokens.
- Call `watch` to wait for a physical event (e.g. `change`, `motion_start`, `motion_stop`, `brightness_above`, `brightness_below`, `color_present`).
- Call `save_baseline` to record known-good reference states.
- Call `compare_to_baseline` to compute SSIM similarity scores and difference heatmaps against reference images.
- Call `read_events` to retrieve timestamped logs from serial ports, logfiles, or MQTT brokers.
- Call `run_action` to invoke allowlisted hardware commands (e.g. board resets or motor stops).

## Policy and Operational Rules

1. Never report physical success without verifying through `capture_image`, `measure`, or `compare_to_baseline`.
2. If `get_status` reports `paused_by_user: true`, ask the user to toggle off "Pause Agent Access" in the dashboard.
3. If an error returns `error_code: "camera_busy"`, advise the user to close other video applications.
4. Keep token consumption bounded: use `measure` for rapid polling and `capture_sequence` for motion timelines.
