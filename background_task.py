import datetime
import json
import os
import shutil
import subprocess
import time
from pysondb.db import JsonDatabase

import common
import task # 引入 task.py 获取 TaskStatus

output_dir = common.getOutputDir()
input_dir = common.getInputDir()

# ==================== 配置外部翻译项目 ====================
# 1. 外部 infer.py 的完整路径 (请根据你的实际情况修改)
INFER_SCRIPT_PATH = os.environ.get("INFER_SCRIPT_PATH", "/content/infer.py")

# 2. infer.py 所在的文件夹 (非常关键！用于让脚本能找到 models 文件夹)
INFER_PROJECT_DIR = os.path.dirname(INFER_SCRIPT_PATH)

# 3. Python 解释器路径 (如果在 Colab 默认就是 python)
PYTHON_EXECUTABLE = os.environ.get("PYTHON_EXECUTABLE", "python")


def call_infer_script(audio_path: str) -> bool:
    """使用 subprocess 调用外部翻译项目的 infer.py 脚本"""
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
        # cwd=INFER_PROJECT_DIR 极其重要：确保 infer.py 能正确找到它的 models 文件夹
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            check=True, 
            cwd=INFER_PROJECT_DIR 
        )
        print("[Exec] 翻译命令执行成功")
        # 如果你想看具体的翻译日志，可以把下面这行解开
        # print(result.stdout)
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

    # 1. 标记状态为转录中
    db.updateById(task_id, {"status": task.TaskStatus.TRASCRIPTING})

    audio_path = os.path.join(input_dir, t['mediaPath'])
    if not os.path.exists(audio_path):
        print(f"[Error] 未找到对应的音频文件: {audio_path}")
        db.updateById(task_id, {"status": task.TaskStatus.ERROR})
        return

    # 2. 调用外部 infer.py 翻译
    success = call_infer_script(audio_path)

    if not success:
        db.updateById(task_id, {"status": task.TaskStatus.ERROR})
        return

    # 3. 定位生成的 lrc 文件
    # 根据 infer.py 第 679 行逻辑：abs_path.parent / f"{abs_path.stem}.{suffix}"
    # 它会将 123.mp3 的后缀切掉，直接生成 123.lrc 在原本的目录下
    base_name = os.path.splitext(t['mediaPath'])[0]
    generated_lrc_path = os.path.join(input_dir, f"{base_name}.lrc")

    # 4. 如果找到了生成好的 LRC，将其移动到输出目录
    if os.path.exists(generated_lrc_path):
        target_lrc_name = f"{task_id}.lrc"
        target_lrc_path = os.path.join(output_dir, target_lrc_name)

        # 把生成的字幕移到输出文件夹
        shutil.move(generated_lrc_path, target_lrc_path)

        db.updateById(task_id, {
            "status": task.TaskStatus.SUCCESS,
            "lrcPath": target_lrc_name,
        })
        print(f"[Success] 任务 {task_id} 转录成功，结果已保存至: {target_lrc_path}")
    else:
        print(f"[Error] 翻译命令已执行，但在目录中未查找到生成的 {generated_lrc_path} 文件")
        db.updateById(task_id, {"status": task.TaskStatus.ERROR})

    # 5. 清理临时下载的音频文件 (不管成功与否都删掉，节省空间)
    if os.path.exists(audio_path):
        os.remove(audio_path)


def run_background_task_infinitly(db: JsonDatabase, wait_seconds: int):
    # 重启服务时，恢复之前未完成的 TRASCRIPTING 任务为 DOWNLOADED
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
