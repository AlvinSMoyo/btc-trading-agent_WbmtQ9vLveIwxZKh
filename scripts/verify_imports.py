import importlib
for m in ["app.strategies.dca","app.strategies.swing_atr","app.indicators.atr","app.risk.stop_watch"]:
    importlib.import_module(m)
print("? imports OK")
