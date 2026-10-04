# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | Yes                |

## Security Model

Agent Cam MCP Tool runs entirely on the local machine:
- Default binding to `127.0.0.1` only.
- Strict `Host` and `Origin` header validation on every HTTP and WebSocket request to prevent DNS rebinding attacks and cross-origin abuse.
- Mutating REST endpoints require a persistent per-installation random token stored in the user data directory with restricted permissions.
- Action runners execute argv lists directly without `shell=True` to eliminate shell injection vulnerabilities.
- Zero telemetry and no outbound network calls other than user-configured IP cameras or MQTT brokers.

## Reporting a Vulnerability

If you discover a security vulnerability within Agent Cam MCP Tool, please report it privately:
- Email: skrelectronicslab@gmail.com
- Do not create public GitHub issues for security vulnerabilities.
- You will receive a response within 48 hours with verification and a coordinated disclosure timeline.
