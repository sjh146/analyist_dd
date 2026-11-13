"""creon-bridge v1: headless localhost JSON HTTP wrapper around Daishin CybosPlus.

Only ``bridge`` package files may import from each other.  pywin32
(``win32com`` / ``pythoncom``) is imported lazily inside functions so the
whole package imports and unit-tests on Linux/CI without pywin32.
"""

__version__ = "1.0.0"
