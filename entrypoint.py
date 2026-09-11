"""PyInstaller's entry script.

`python -m pomodoro_guardian` can't be pointed at directly: PyInstaller
needs a plain script, and `__main__.py`'s relative import (`from .app`)
only resolves when it's actually run as part of the package. This just
does the equivalent import as an absolute one instead.
"""

import sys

from pomodoro_guardian.app import main

if __name__ == "__main__":
    sys.exit(main())
