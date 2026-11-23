import os
from pathlib import Path


def crash_if_armed(name):
    if os.getenv("PAYMENT_RECOVERY_DRILL") != "isolated":
        return
    marker = Path("/tmp") / name
    try:
        marker.unlink()
    except FileNotFoundError:
        return
    print(f"Recovery drill reached {name}", flush=True)
    os._exit(86)
