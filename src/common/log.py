def info(msg: str):
    print(msg, flush=True)

def warn(msg: str):
    print(f"⚠️ {msg}", flush=True)

def err(msg: str):
    print(f"❌ {msg}", flush=True)
