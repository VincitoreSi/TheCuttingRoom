"""Ensure the component root is importable under pytest so `import engine` and
`import tests.*` resolve regardless of where pytest is invoked from."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
