"""OpenAI Realtime (speech-to-speech over WebRTC) voice mode.

Audio flows directly between the kiosk and OpenAI. This backend:
- creates each call with the permanent API key (the kiosk never sees it),
- holds a server-side "sideband" WebSocket into the call to run business tools
  with trusted kiosk context, enforce idle/max-duration limits and log metrics.

The business tools themselves are the same ones the chained mode uses.
"""
