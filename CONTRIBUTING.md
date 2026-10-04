# Contributing to Agent Cam MCP Tool

Thank you for your interest in contributing to Agent Cam MCP Tool.

## Architecture Guidelines

1. **Evidence over claims:** Never submit code that relies on synthetic mocks for production paths.
2. **Never hang:** Every I/O operation (camera probe, capture, serial, socket, subprocess) must have an explicit timeout.
3. **Never crash the server:** Errors inside tools must return structured errors rather than unhandled exceptions.
4. **stdout is sacred in stdio mode:** In stdio mode, all logging must go to stderr or file logs.
5. **Strict UI style:** Flat surfaces, 1px borders, solid accent color. No emojis, gradients, glows, or decorative illustrations.

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/skr-electronics-lab/agent-cam-mcp.git
   cd agent-cam-mcp
   ```

2. Create a virtual environment and install with dev dependencies:
   ```bash
   uv venv
   uv pip install -e ".[all,dev]"
   ```

3. Run the test suite:
   ```bash
   pytest
   ```

4. Run code formatting and lint checks:
   ```bash
   ruff check .
   ruff format --check .
   ```

5. Run doctor diagnostics against the fake camera:
   ```bash
   python -m agent_cam.cli --fake-camera doctor
   ```

## Pull Request Process

1. Create a feature branch with a descriptive name.
2. Ensure all tests pass (`pytest`) and linting is clean (`ruff check .`).
3. Add unit or integration tests for any new features or bug fixes.
4. Submit a pull request with a concise conventional commit message.
