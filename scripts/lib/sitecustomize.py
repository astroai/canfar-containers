"""AstroAI Python runtime site customization.

Automatically loaded during Python initialization if /opt/astroai/lib is in
PYTHONPATH or sys.path. Enables zero-configuration project environment discovery
for notebooks and interactive sessions.
"""

try:
    import canfar_marimo

    canfar_marimo.enable_auto_environment()
except Exception:
    pass
