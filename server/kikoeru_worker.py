from typing import Tuple, Dict
import json
import os
import time
import datetime
import common
import requests
import math
import subprocess

db_dir = common.getDbDir()
task_file_path = common.getTaskFilePath()
input_audio_dir = common.getInputDir()
worker_name = common.getWorkerName()
worker_idle_seconds = common.getBackgroundIdleSeconds()
kikoeru_url = common.getKikoeruUrl()
kikoeru_user = common.getKikoeruUser()
kikoeru_password = common.getKikoeruPassword()
is_need_auth = False

# 使用session进行通信，保存token，每一次运行前检查token是否失效，如果失效，需要重新登陆验证
def setupSession(session:requests.Session, token:str):
    headers = {}
    if token != "":
        headers["Authorization"] = f"Bearer {token}"
    session.headers = headers

session = requests.session()
setupSession(session, common.getToken())

def checkKikoeruAuth(url):
    response = session.get(f"{url}/api/auth/me")
    if response.status_code == 200:
        print("当前状态下kikoeru服务器可直接通信")
        return False
    elif response.status_code == 401:
        print("kikoeru服务器需要用户验证")
        return True
    else:
        print(response)
        raise Exception(f"检查服务器登陆时，发生未知错误：{response.status_code}")

def loginKikoeru(url:str, user:str, password:str)->str:
    print("尝试登陆获取token")
    response = session.post(
        f"{url}/api/auth/me",
        {
            "name": user,
            "password": password,
        }
    )
    if response.status_code == 200:
        print("登陆成功")
        kikoeru_token = response.json()['token']
        print("token = ", kikoeru_token)
        return kikoeru_token
    else:
        print("登陆失败")
        return ""

def acquireTask(url:str)->Tuple[bool, bool, Dict]:
    try:
        res = session.post(
            f"{url}/api/lyric/translate/acquire",
            {
                "worker_name": worker_name,
            }
        )
        no_task_can_acquire = res.status_code == 404
        success = res.status_code == 200
        data = res.json()
    except:
        return [False, True, None]
    return (success, no_task_can_acquire, data)

def sleepAndWait(secs:int, info):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\r{info} ({now}) wait for another {secs} seconds", end="")
    time.sleep(secs)

def updateTaskStatus(task:Dict, status:str)->bool:
    try:
        res = session.post(
            f"{kikoeru_url}/api/lyric/translate/status",
            {
                **task,
                "worker_status": status,
            }
        )
        print("  任务进度：", status)
        return res.json()['success']
    except Exception as e:
        print("updateTaskStatus error: ", e)

def downloadAudioFile(task:Dict, save_name:str)->bool:
    try:
        r = session.get(f"{kikoeru_url}/api/lyric/translate/download", params={
            "id": task["id"],
            "secret": task["secret"],
        }, stream=True)
        with open(os.path.join(input_audio_dir, save_name), 'wb') as fd:
            for chunk in r.iter_content(chunk_size=128):
                fd.write(chunk)
        return True
    except Exception as e:
        print("下载音频失败：", e)
        return False

def finishTask(task:Dict, success:bool, lrc_content:str):
    print("上传任务结果")
    try:
        r = session.post(f"{kikoeru_url}/api/lyric/translate/finish", json={
            "id": task["id"],
            "secret": task["secret"],
            "success": success,
            "lrc_content": lrc_content,
        })
        if r.status_code != 200:
            print("上传任务结果失败", r.json())
    except Exception as e:
        print("上传任务结果失败：", e)
        return False

def saveTaskToFile(task:Dict):
    with open(task_file_path, "w", encoding="utf8") as f:
        json.dump(task, f, indent=4)

# ==================== 翻译调用逻辑 ====================
def transcribe_audio(audio_path:str)->str:
    INFER_SCRIPT_PATH = os.environ.get("INFER_SCRIPT_PATH", "/content/Faster-Whisper-TransWithAI-ChickenRice/infer.py")
    INFER_PROJECT_DIR = os.path.dirname(INFER_SCRIPT_PATH)
    PYTHON_EXECUTABLE = os.environ.get("PYTHON_EXECUTABLE", "python")

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

    print(f"[Exec] 执行翻译命令: {' '.join(cmd)}")
    try:
        # 【核心修改】：去掉了 capture_output=True 和 text=True
        # 这样 infer.py 的实时进度条和所有 print 日志都会直接显示在 Colab 屏幕上
        subprocess.run(
            cmd,
            check=True,
            cwd=INFER_PROJECT_DIR
        )
        print("[Exec] 翻译命令执行成功")
    except subprocess.CalledProcessError as e:
        print(f"[Error] infer.py 执行失败，退出码: {e.returncode}")
        raise e

    # 读取生成的字幕文件内容
    base_name = os.path.splitext(audio_path)[0]
    lrc_path = f"{base_name}.lrc"

    if os.path.exists(lrc_path):
        with open(lrc_path, 'r', encoding='utf-8') as f:
            lrc_content = f.read()
        # 清理本地的临时字幕文件
        os.remove(lrc_path)
        return lrc_content
    else:
        raise Exception(f"未找到生成的LRC文件: {lrc_path}")


def checkTaskIsOwnByMe(task:Dict)->bool:
    try:
        r = session.get(f"{kikoeru_url}/api/lyric/translate/get", params={
            "id": task["id"],
            "secret": task["secret"],
        })
        if r.status_code == 200:
            data = r.json()
            print("get task status, data = ", data)
            return 'task' in data
        else:
            return False
    except Exception as e:
        print("检查任务状态失败：", e)
        raise e

def processTask(task):
    print("存储task信息到本地文件中")
    saveTaskToFile(task)

    if not checkTaskIsOwnByMe(task):
        print("服务器上的翻译任务已被删除，或者已经被重新启动翻译进程，跳过当前任务")
        os.unlink(task_file_path)

        if 'audio_file_name' in task:
            print("删除本地音频文件")
            os.unlink(os.path.join(input_audio_dir, task['audio_file_name']))
        return

    if 'audio_file_name' not in task:
        print("下载音频文件")
        audio_file_name = f"{task['id']}{task['audio_ext']}"
        if not downloadAudioFile(task, audio_file_name):
            finishTask(task, False, "音频下载失败")
            os.unlink(task_file_path)
            return
        task['audio_file_name'] = audio_file_name
        saveTaskToFile(task)
    else:
        audio_file_name = task['audio_file_name']

    audio_file_path = os.path.join(input_audio_dir, audio_file_name)
    print("音频文件位于：", audio_file_path)

    success = False
    lrc_content = ""
    try:
        print("翻译中...")
        lrc_content = transcribe_audio(audio_file_path)
        success = True
        print("翻译成功")
    except Exception as e:
        print("transcripting error, ", e)
        success = False

    finishTask(task, success, lrc_content)

    print(" 任务完成，删除本地记录")
    if os.path.exists(audio_file_path):
        os.unlink(audio_file_path)
    if os.path.exists(task_file_path):
        os.unlink(task_file_path)

def clearOldTaskAtStartup():
    print("尝试处理上一次没有完成的翻译任务")
    if not os.path.exists(task_file_path):
        print("没有遗留的未完成任务，继续正常运行")
        return

    print("发现有未完成的任务，加载并执行")
    with open(task_file_path, "r", encoding="utf8") as f:
        task = json.load(f)
    processTask(task)

def load_model():
    print("【拦截】已跳过加载原有的 Whisper 模型，后续处理将直接调用独立的 infer.py 外部脚本")

def main():
    print("hello world: ")
    print("kikoeru_url = ", kikoeru_url)
    print("kikoeru_user = ", kikoeru_user)
    print("kikoeru_password = ", kikoeru_password)
    print("this translate worker name is: ", worker_name)

    global is_need_auth
    global kikoeru_token

    is_need_auth = checkKikoeruAuth(kikoeru_url)

    if is_need_auth:
        kikoeru_token = loginKikoeru(kikoeru_url, kikoeru_user, kikoeru_password)
        if kikoeru_token != "":
            common.saveToken(kikoeru_token)
            setupSession(session, kikoeru_token)

    load_model()
    clearOldTaskAtStartup()

    while True:
        success, run_out_of_task, task = acquireTask(kikoeru_url)

        if run_out_of_task:
            sleepAndWait(worker_idle_seconds, "翻译队列为空")
        elif not success:
            sleepAndWait(worker_idle_seconds, f"发生错误(${task})")
        else:
            print("")
            print("task.id = ", task['id'], "task.secret = ", task['secret'])
            processTask(task)

if __name__ == "__main__":
    main()