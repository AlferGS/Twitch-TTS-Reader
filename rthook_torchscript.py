import inspect
import os
import sys

if getattr(sys, 'frozen', False):
    _base = sys._MEIPASS
    _orig_getsourcefile = inspect.getsourcefile

    def _frozen_getsourcefile(obj):
        try:
            f = _orig_getsourcefile(obj)
        except (TypeError, OSError):
            f = None
        if f and not os.path.isabs(f) and not os.path.exists(f):
            cand = os.path.join(_base, f.replace('/', os.sep))
            if os.path.exists(cand):
                return cand
        return f

    inspect.getsourcefile = _frozen_getsourcefile