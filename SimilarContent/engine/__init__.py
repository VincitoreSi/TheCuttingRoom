"""similar-content — the clone producer's render engine.

Stdlib only (plus system ffmpeg), mirroring AnalysisEngine: hand-rolled urllib clients, no
SDKs, no media libraries. Everything reaches the backend over HTTP through engine.hub.
"""
AGENT_NAME = "similar-content"
KIND = "clone"
