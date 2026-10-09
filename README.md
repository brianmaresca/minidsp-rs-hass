# Homeassistant MiniDSP

Requires a running minidsp-rs instance with its HTTP server enabled.

Copy `custom_components/minidsp` into Home Assistant's `custom_components`
directory, restart Home Assistant, and add the MiniDSP integration.

Enter the server URL, including its port, for example `http://192.168.2.151:5380`.
The integration converts HTTP URLs to WebSocket URLs for live updates. Existing
`ws://` and `wss://` URLs are also accepted. You can change the server URL from
the integration's options screen.

Output-gain controls use assumed state: their values are unknown until you
successfully set them through Home Assistant. The minidsp-rs status API does not
report output gains, so values set by other applications cannot be read back.
Assumed gains are cleared after a preset change, connection loss, or reload.

## Development checks

Install `requirements-test.txt` in a Python virtual environment and run
`python -m unittest discover -s tests -v`. These tests use Home Assistant interface
stubs and simulated WebSocket connections; they do not require or control hardware.
