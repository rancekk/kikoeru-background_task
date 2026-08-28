import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server.kikoeru_worker import main as worker_main

if __name__ == "__main__":
    try:
        worker_main()
    except KeyboardInterrupt:
        print("\n[Worker] 正常退出。")
