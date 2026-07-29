import datetime
import json
import os
import shutil
import subprocess
import time
from pysondb.db import JsonDatabase

import common
import task

output_dir = common.getOutputDir()
input_dir = common.getInputDir()

# 配置外部翻译项目
INFER_SCRIPT_PATH = os.environ.get("INFER_SCRIPT_PATH", "/content/infer.py")
INFER_PROJECT_DIR = os.path.dirname(INFER_SCRIPT_PATH)
PYTHON_EXECUTABLE = os.environ.get("PYTHON_EXECUTABLE", "python")

def call_infer_script(audio_path: str) -> bool:
    cmd = [
        PYTHON_EXECUTABLE,
        INFER_SCRIPT_PATH,
        '--audio_suffixes=mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv,opus',
        '--sub_formats=lrc',
        '--device=cuda',
        '--task=translate',
        '--enable_batching',
        '--max_batch_size', '16',
        audio_path
    ]

    print(f"[Exec] 开始执行翻译命令: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            cwd=INFER_PROJECT_DIR
        )
        print("[Exec] 翻译命令执行成功")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[Error] infer.py 执行失败，退出码: {e.returncode}")
        print(f"[Error] 错误日志: {e.stderr}")
        return False
    except Exception as e:
        print(f"[Error] 调用命令行时发生异常: {e}")
        return False

def process_task(t, db: JsonDatabase):
    task_id = t['id']
    print(f"\n--- 开始处理任务 id = {task_id} ---")

    db.updateById(task_id, {"status": task.TaskStatus.TRASCRIPTING})

    audio_path = os.path.join(input_dir, t['mediaPath'])
    if not os.path.exists(audio_path):
        print(f"[Error] 未找到对应的音频文件: {audio_path}")
        db.updateById(task_id, {"status": task.TaskStatus.ERROR})
        return

    success = call_infer_script(audio_path)

    if not success:
        db.updateById(task_id, {"status": task.TaskStatus.ERROR})
        return

    base_name = os.path.splitext(t['mediaPath'])[0]
    generated_lrc_path = os.path.join(input_dir, f"{base_name}.lrc")

    if os.path.exists(generated_lrc_path):
        target_lrc_name = f"{task_id}.lrc"
        target_lrc_path = os.path.join(output_dir, target_lrc_name)

        shutil.move(generated_lrc_path, target_lrc_path)

        db.updateById(task_id, {
            "status": task.TaskStatus.SUCCESS,
            "lrcPath": target_lrc_name,
        })
        print(f"[Success] 任务 {task_id} 转录成功，结果已保存至: {target_lrc_path}")
    else:
        print(f"[Error] 翻译命令已执行，但在目录中未查找到生成的 {generated_lrc_path} 文件")
        db.updateById(task_id, {"status": task.TaskStatus.ERROR})

    if os.path.exists(audio_path):
        os.remove(audio_path)

def run_background_task_infinitly(db: JsonDatabase, wait_seconds: int):
    tasks = db.getByQuery({"status": task.TaskStatus.TRASCRIPTING})
    for t in tasks:
        print(f"重置中途卡住的任务 id = {t['id']} -> DOWNLOADED")
        db.updateById(t['id'], {"status": task.TaskStatus.DOWNLOADED})

    while True:
        datas = db.getByQuery({"status": task.TaskStatus.DOWNLOADED})

        if len(datas) == 0:
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\r等待新任务({now}) ...", end="")
            time.sleep(wait_seconds)
            continue

        front_task = datas[0]
        process_task(front_task, db)

def main():
    db = common.getDbInstance()
    bg_idle_secs = common.getBackgroundIdleSeconds()
    print("后台翻译调度服务启动，等待下载完成的任务...")
    print(f"已配置推理脚本路径: {INFER_SCRIPT_PATH}")
    run_background_task_infinitly(db, bg_idle_secs)

if __name__ == "__main__":
    main()