import time
import urllib.error
import urllib.request

for attempt in range(50):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=1) as response:
            if response.status == 200:
                break
    except (OSError, urllib.error.URLError):
        time.sleep(.1)
else:
    raise SystemExit("Local API did not become healthy")
