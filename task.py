import time

class TaskStatus:
    NONE = 0 # 非法状态
    PENDING = 1 # 待执行
    DOWNLOADING = 2 # 正在下在音频
    DOWNLOADED = 3 # 下载完成
    TRASCRIPTING = 4 # 转录中
    SUCCESS = 5 # 转录成功
    ERROR = 6 # 转录失败

def createNewTask(resourceUrl:str, displayName:str)->object:
    return {
        "status": TaskStatus.PENDING,
        "resourceUrl": resourceUrl,
        "displayName": displayName,
        "createdTime": time.time(),
        "mediaPath": "",
        "lrcPath": "",
    }