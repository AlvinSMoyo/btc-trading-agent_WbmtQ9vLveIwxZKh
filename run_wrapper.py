import os, runpy, traceback, sys
print("STATE_DIR=", os.environ.get("STATE_DIR"), flush=True)
try:
    runpy.run_module("app.runner", run_name="__main__")
    print("Runner returned normally", flush=True)
except SystemExit as e:
    print("SystemExit:", e.code, flush=True)
    raise
except Exception:
    print("=== Python exception ===", flush=True)
    traceback.print_exc()
    sys.exit(1)
