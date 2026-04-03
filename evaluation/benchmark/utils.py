import json
import os
import psutil
import time
from typing import List, Dict, Any

def load_json(filepath: str) -> List[Dict[str, Any]]:
    """Loads data from a JSON file."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_memory_usage_mb() -> float:
    """Returns the current process memory usage in MB."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 * 1024)

class Timer:
    """Context manager for timing execution."""
    def __init__(self):
        self.start = 0
        self.end = 0
        self.interval = 0

    def __enter__(self):
        self.start = time.time()
        return self

    def __exit__(self, *args):
        self.end = time.time()
        self.interval = self.end - self.start
