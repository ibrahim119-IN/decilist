# Security scope

This alpha is a local development runtime. Keep it on loopback; do not expose it as an authenticated production service. Third-party providers are trusted executable plugins. `local_only` enforces their declared locality, not a process-level firewall.

Do not paste secrets or sensitive production data into the demo. Native model code is pinned and requires explicit opt-in. Input and exception text are omitted from normal decision traces, but the caller still owns returned data and its storage.

Use GitHub's private vulnerability reporting if enabled, or contact the maintainer through their GitHub profile to arrange a private report. Do not post credentials or exploit payloads containing private data in public issues. There is no response-time guarantee for this early project.
