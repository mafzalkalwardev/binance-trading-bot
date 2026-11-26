"""Crash-safe JSON state (atomic write via temp file + os.replace)."""
import json, os, tempfile
from pathlib import Path

DEFAULT = {"positions": {}, "pending": {}, "last_candle": {}, "day": None, "day_start_equity": None,
           "paused_day": None, "halted": False, "started_at": None, "sim": None}

class State:
    def __init__(self, path: Path):
        self.path = Path(path); self.data = json.loads(json.dumps(DEFAULT))
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))
    def __getitem__(self, k): return self.data[k]
    def __setitem__(self, k, v): self.data[k] = v
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2, default=str); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, self.path)
