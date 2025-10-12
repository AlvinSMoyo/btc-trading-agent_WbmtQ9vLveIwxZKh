import pandas as pd, numpy as np, os
TR=r".\state\trades.csv"
assert os.path.exists(TR), "Missing trades.csv"
df=pd.read_csv(TR)
df.columns=[c.strip().lower() for c in df.columns]

tcol=None
for c in ["ts_dt","ts_utc","timestamp","date","datetime","ts"]:
    if c in df.columns: tcol=c; break
assert tcol, "No timestamp-like column in trades.csv"

s=df[tcol]
def to_dt(x):
    if x.dtype==object and x.dropna().astype(str).str.contains("202").any():
        return pd.to_datetime(x, errors="coerce", utc=True)
    y=pd.to_numeric(x, errors="coerce")
    m=np.nanmax(y)
    if m<1e6:        # e.g. 1.757 -> treat as seconds*1e9 (ns)
        return pd.to_datetime((y*1e9).round(), unit="ns", utc=True)
    if m<1e11:       # seconds
        return pd.to_datetime(y, unit="s", utc=True)
    if m<1e14:       # milliseconds
        return pd.to_datetime(y, unit="ms", utc=True)
    if m<1e17:       # microseconds
        return pd.to_datetime(y, unit="us", utc=True)
    return pd.to_datetime(y, unit="ns", utc=True)

dt=to_dt(s)
df["ts_utc"]=dt
df["ts_dt"]=dt.dt.tz_convert("UTC").dt.strftime("%Y-%m-%d %H:%M:%S")
df=df[df["ts_utc"].dt.year.ge(2009)]  # drop any bogus epoch rows
df.to_csv(TR, index=False)
print("? fixed trades.csv rows:", len(df))
