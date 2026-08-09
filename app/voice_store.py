"""文字版与语音通话之间的共享状态：待执行的定制面试（本地单用户场景）。

文字版生成定制面试题后写入，语音服务接通时读取并按该题目开始模拟面试；
可通过 clear_custom_interview() 清除（生成新的会覆盖旧值）。
"""

import json
import os
import time
from contextlib import suppress

import app.config as config

_CUSTOM_FILE = config.DATA_DIR / "voice_custom_interview.json"


def save_custom_interview(job_title: str, jd: str, questions: list[str]) -> None:
    """保存最新一份定制面试（原子写入，覆盖旧值）。"""
    config.ensure_data_dir()
    payload = {
        "job_title": job_title,
        "jd": jd,
        "questions": [q for q in (questions or []) if q and q.strip()],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = _CUSTOM_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _CUSTOM_FILE)  # 原子替换，避免语音服务读到半截文件


def load_custom_interview() -> dict | None:
    """读取最新定制面试；不存在、损坏或没有题目时返回 None。"""
    try:
        data = json.loads(_CUSTOM_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not data.get("questions"):
        return None
    return data


def clear_custom_interview() -> None:
    """清除已保存的定制面试（语音面试完成或用户取消时调用）。"""
    with suppress(OSError):
        _CUSTOM_FILE.unlink(missing_ok=True)
