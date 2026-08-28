from typing import Tuple, Dict, Optional
import json
import os
import sys
import time
import datetime
import requests
import subprocess

# 确保能正常引用上一层的 common 模块
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
import common

input_audio_dir = common.getInputDir()
worker_name = common.getWorkerName()
worker_idle_seconds = common.getBackgroundIdleSeconds()
kikoeru_url = common.getKikoeruUrl()
kikoeru_user = common.getKikoeruUser()
kikoeru_password = common.getKikoeruPassword()
is_need_auth = False

session = requests.session()

def setupSession(sess: requests.Session, token: str):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    sess.headers.update(headers)

# 初始化读取本地 token
saved_token = common.getToken()
if saved_token:
    setupSession(session, saved_token)

def checkKikoeruAuth(url: str) -> bool:
    try:
        response = session.get(f"{url}/api/auth/me", timeout=15)
        if response.status_code == 200:
            print("当前状态下 kikoeru 服务器可直接通信（已认证）")
            return False
        elif response.status_code == 401:
            print("kikoeru 服务器需要用户验证")
            return True
        else:
            print(f"检查服务器登录时状态码异常: {response.status_code}")
            return True
    except Exception as e:
        print(f"连接 kikoeru 服务器失败: {e}")
        return True

def loginKikoeru(url: str, user: str, password: str) -> str:
    print(f"尝试登录获取 token (用户: {user})...")
    try:
        response = session.post(
            f"{url}/api/auth/me",
            json={"name": user, "password": password},
            timeout=15
        )
        if response.status_code == 200:
            print("登录成功")
            kikoeru_token = response.json().get('token', '')
            if kikoeru_token:
                common.saveToken(kikoeru_token)
                setupSession(session, kikoeru_token)
            return kikoeru_token
        else:
            print(f"登录失败，状态码: {response.status_code}，响应: {response.text}")
            return ""
    except Exception as e:
        print(f"登录请求发生异常: {e}")
        return ""

def ensureAuthenticated():
    """遇到 401 时自动重新登录刷新 Token"""
    if is_need_auth:
        token = loginKikoeru(kikoeru_url, kikoeru_user, kikoeru_password)
        return bool(token)
    return True

def acquireTask(url: str) -> Tuple[bool, bool, Optional[Dict]]:
    try:
        res = session.post(
            f"{url}/api/lyric/translate/acquire",
            json={"worker_name": worker_name},
            timeout=20
        )
        if res.status_code == 200:
            return (True, False, res.json())
        elif res.status_code == 404:
            return (False, True, None)
        elif res.status_code == 401:
            print("\n[Auth] Token 过期，尝试重新登录...")
            if ensureAuthenticated():
                return acquireTask(url)
            return (False, False, {"error": "Token expired and re-login failed"})
        else:
            print(f"\n[Warning] 获取任务响应异常，HTTP状态码: {res.status_code}")
            return (False, False, {"status_code": res.status_code})
    except Exception as e:
        print(f"\n[Error] 获取任务网络异常: {e}")
        return (False, False, {"exception": str(e)})

def sleepAndWait(secs: int, info: str):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\r{info} ({now}) wait for another {secs} seconds", end="", flush=True)
    time.sleep(secs)

def updateTaskStatus(task: Dict, status: str) -> bool:
    try:
        res = session.post(
            f"{kikoeru_url}/api/lyric/translate/status",
            json={**task, "worker_status": status},
            timeout=15
        )
        if res.status_code == 200:
            print(f"  任务进度: {status}")
            return res.json().get('success', False)
        return False
    except Exception as e:
        print(f"updateTaskStatus error: {e}")
        return False

def downloadAudioFile(task: Dict, save_name: str) -> bool:
    target_path = os.path.join(input_audio_dir, save_name)
    temp_path = target_path + ".tmp"
    try:
        r = session.get(
            f"{kikoeru_url}/api/lyric/translate/download",
            params={"id": task["id"], "secret": task["secret"]},
            stream=True,
            timeout=300
        )
        if r.status_code != 200:
            print(f"下载音频失败，HTTP状态码: {r.status_code}，可能文件不存在或无权限")
            return False

        with open(temp_path, 'wb') as fd:
            for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1MB buffer
                if chunk:
                    fd.write(chunk)

        # 下载完整后原子重命名
        if os.path.exists(target_path):
            os.remove(target_path)
        os.rename(temp_path, target_path)
        return True
    except Exception as e:
        print(f"下载音频异常: {e}")
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return False

def finishTask(task: Dict, success: bool, lrc_content: str) -> bool:
    print("正在上传任务结果...")
    for retry in range(3):
        try:
            r = session.post(
                f"{kikoeru_url}/api/lyric/translate/finish",
                json={
                    "id": task["id"],
                    "secret": task["secret"],
                    "success": success,
                    "lrc_content": lrc_content,
                },
                timeout=45
            )
            if r.status_code == 200:
                print("✓ 任务结果上传成功")
                return True
            elif r.status_code == 401:
                ensureAuthenticated()
            else:
                print(f"上传任务结果失败 (重试 {retry+1}/3)，状态码: {r.status_code}")
        except Exception as e:
            print(f"上传任务结果网络异常 (重试 {retry+1}/3): {e}")
        time.sleep(2)
    return False

def saveTaskToFile(task: Dict):
    task_file_path = common.getTaskFilePath()
    with open(task_file_path, "w", encoding="utf8") as f:
        json.dump(task, f, indent=4, ensure_ascii=False)

def transcribe_audio(audio_path: str) -> str:
    INFER_SCRIPT_PATH = os.environ.get("INFER_SCRIPT_PATH", "/content/Faster-Whisper-TransWithAI-ChickenRice/infer.py")
    INFER_PROJECT_DIR = os.path.dirname(INFER_SCRIPT_PATH)
    PYTHON_EXECUTABLE = os.environ.get("PYTHON_EXECUTABLE", "python")

    # 使用项目原生参数调用
    cmd = [
        PYTHON_EXECUTABLE,
        INFER_SCRIPT_PATH,
        '--audio_suffixes=mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv,opus',
        '--sub_formats=lrc',
        '--device=cuda',
        '--task=translate',
        '--enable_batching',
        '--max_batch_size', '8',
        audio_path
    ]

    print(f"[Exec] 执行翻译命令: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, cwd=INFER_PROJECT_DIR)
        print("[Exec] 翻译命令执行成功")
    except subprocess.CalledProcessError as e:
        print(f"[Warning] batch 推理失败 (code: {e.returncode})，尝试降级单批次模式重试...")
        fallback_cmd = [
            PYTHON_EXECUTABLE,
            INFER_SCRIPT_PATH,
            '--audio_suffixes=mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv,opus',
            '--sub_formats=lrc',
            '--device=cuda',
            '--task=translate',
            audio_path
        ]
        try:
            subprocess.run(fallback_cmd, check=True, cwd=INFER_PROJECT_DIR)
            print("[Exec] 降级单批次翻译执行成功")
        except subprocess.CalledProcessError as e2:
            print(f"[Error] infer.py 再次执行失败，退出码: {e2.returncode}")
            raise e2

    base_name = os.path.splitext(audio_path)[0]
    lrc_path = f"{base_name}.lrc"

    if os.path.exists(lrc_path):
        with open(lrc_path, 'r', encoding='utf-8') as f:
            lrc_content = f.read()
        try:
            os.remove(lrc_path)
        except Exception:
            pass
        return lrc_content
    else:
        raise Exception(f"未找到生成的LRC文件: {lrc_path}")

def checkTaskIsOwnByMe(task: Dict) -> bool:
    try:
        r = session.get(
            f"{kikoeru_url}/api/lyric/translate/get",
            params={"id": task["id"], "secret": task["secret"]},
            timeout=15
        )
        if r.status_code == 200:
            data = r.json()
            return 'task' in data
        return False
    except Exception as e:
        print(f"检查任务状态失败: {e}")
        return True

def processTask(task: Dict):
    task_file_path = common.getTaskFilePath()
    print("存储 task 信息到本地文件中")
    saveTaskToFile(task)

    if not checkTaskIsOwnByMe(task):
        print("服务器上的翻译任务已被删除或重置，跳过当前任务")
        if os.path.exists(task_file_path):
            os.unlink(task_file_path)
        if 'audio_file_name' in task:
            audio_file = os.path.join(input_audio_dir, task['audio_file_name'])
            if os.path.exists(audio_file):
                os.unlink(audio_file)
        return

    ext = task.get('audio_ext', '.opus')
    if not ext.startswith('.'):
        ext = '.' + ext
    audio_file_name = task.get('audio_file_name', f"{task['id']}{ext}")
    audio_file_path = os.path.join(input_audio_dir, audio_file_name)

    if not os.path.exists(audio_file_path):
        print(f"下载音频文件: {audio_file_name}")
        if not downloadAudioFile(task, audio_file_name):
            finishTask(task, False, "音频下载失败")
            if os.path.exists(task_file_path):
                os.unlink(task_file_path)
            return
        task['audio_file_name'] = audio_file_name
        saveTaskToFile(task)

    print("音频文件位于:", audio_file_path)

    success = False
    lrc_content = ""
    try:
        print("翻译中...")
        lrc_content = transcribe_audio(audio_file_path)
        success = True
        print("翻译成功")
    except Exception as e:
        print("transcribing error:", e)
        success = False
        lrc_content = f"翻译异常: {e}"

    uploaded = finishTask(task, success, lrc_content)

    if uploaded:
        print("任务完成并已上报，清理本地缓存")
        if os.path.exists(audio_file_path):
            try:
                os.unlink(audio_file_path)
            except Exception:
                pass
        if os.path.exists(task_file_path):
            try:
                os.unlink(task_file_path)
            except Exception:
                pass
    else:
        print("[Warning] 任务结果上传失败，保留本地 task.json 记录以便重试")

def clearOldTaskAtStartup():
    task_file_path = common.getTaskFilePath()
    print("尝试检查上一次未完成的翻译任务...")
    if not os.path.exists(task_file_path):
        print("没有遗留的未完成任务，继续正常运行")
        return

    try:
        print("发现有未完成的任务记录，加载并执行...")
        with open(task_file_path, "r", encoding="utf8") as f:
            task = json.load(f)
        processTask(task)
    except Exception as e:
        print(f"[Warning] 恢复历史任务异常: {e}，清理旧记录")
        try:
            os.unlink(task_file_path)
        except Exception:
            pass

def main():
    print("=" * 60)
    print("Kikoeru Background Translate Worker 启动")
    print("kikoeru_url      =", kikoeru_url)
    print("kikoeru_user     =", kikoeru_user)
    print("worker_name      =", worker_name)
    print("=" * 60)

    global is_need_auth
    is_need_auth = checkKikoeruAuth(kikoeru_url)

    if is_need_auth:
        token = loginKikoeru(kikoeru_url, kikoeru_user, kikoeru_password)
        if not token:
            print("[Error] 无法完成身份验证，请检查用户名和密码配置！")
            return

    clearOldTaskAtStartup()

    while True:
        try:
            success, run_out_of_task, task = acquireTask(kikoeru_url)

            if run_out_of_task:
                sleepAndWait(worker_idle_seconds, "翻译队列为空")
            elif not success:
                sleepAndWait(worker_idle_seconds, f"获取任务失败({task})")
            else:
                print("\n" + "=" * 60)
                print(f"获取到新任务: id={task.get('id')}, work_id={task.get('work_id')}")
                processTask(task)
        except KeyboardInterrupt:
            print("\n[Worker] 收到退出信号，安全终止。")
            break
        except Exception as e:
            print(f"\n[Worker Loop Error] 未捕获异常: {e}")
            time.sleep(worker_idle_seconds)

if __name__ == "__main__":
    main()
