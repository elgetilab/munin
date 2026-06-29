"""Put the benchmarks root on sys.path so `import munin_bench` works whether
pytest is invoked from the repo root or from backend/benchmarks/."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
