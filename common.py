import os
import pysondb
from pysondb.db import JsonDatabase

def getDbDir() -> str:
    p = os.environ.get("DB_PATH", "./db")
    abs_p = os.path.abspath(p)
    if not os.path.exists(abs_p):
        os.makedirs(abs_p, exist_ok=True)
    return abs_p

def getDbInstance() -> JsonDatabase:
    db_path = getDbDir()
    path = os.path.join(db_path, "db.json")
    return pysondb.db.getDb(path)

def getInputDir() -> str:
    p = os.environ.get("INPUT_PATH", "./cache/input")
    abs_p = os.path.abspath(p)
    if not os.path.exists(abs_p):
        os.makedirs(abs_p, exist_ok=True)
    return abs_p

def getOutputDir() -> str:
    p = os.environ.get("OUTPUT_PATH", "./cache/output")
    abs_p = os.path.abspath(p)
    if not os.path.exists(abs_p):
        os.makedirs(abs_p, exist_ok=True)
    return abs_p

def getModelPath() -> str:
    p = os.environ.get("MODEL_PATH", "./cache/model")
    abs_p = os.path.abspath(p)
    if not os.path.exists(abs_p):
        os.makedirs(abs_p, exist_ok=True)
    return abs_p

def getBackgroundIdleSeconds() -> int:
    s = os.environ.get("BG_TASK_WAIT_SECS", "5")
    try:
        return int(s)
    except ValueError:
        return 5

def getTranscribeDevice() -> str:
    return os.environ.get("TRANSCRIBE_DEVICE", "auto")

def getServerPort() -> int:
    return int(os.environ.get("PORT", "8820"))

def getKikoeruUrl() -> str:
    url = os.environ.get("KIKOERU_URL", None)
    if url is None:
        raise Exception("kikoeru url not configured")
    return url.rstrip("/")  # remove trailing /

def getKikoeruUser() -> str:
    return os.environ.get("KIKOERU_USER", "")

def getKikoeruPassword() -> str:
    return os.environ.get("KIKOERU_PASSWORD", "")

def getToken() -> str:
    p = getDbDir()
    token_file = os.path.join(p, "token")
    if not os.path.exists(token_file):
        return ""
    try:
        with open(token_file, "r", encoding="utf8") as f:
            return f.readline().strip()
    except Exception:
        return ""

def saveToken(token: str):
    p = getDbDir()
    token_file = os.path.join(p, "token")
    try:
        with open(token_file, "w", encoding="utf8") as f:
            f.write(token)
    except Exception as e:
        print(f"[Warning] 保存token失败: {e}")

def getWorkerName() -> str:
    return os.environ.get("WORKER_NAME", "default_worker")

def getTaskFilePath() -> str:
    return os.path.join(getDbDir(), "task.json")
