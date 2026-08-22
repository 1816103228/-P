"""文字版与语音通话之间的共享状态：待执行的定制面试（按用户隔离）。

文字版生成定制面试题后按 user_id 写入，语音接通时按 user_id 读取并按该题目开始模拟面试；
可通过 clear_custom_interview(user_id) 清除（生成新的会覆盖旧值）。

多用户改造后由 SQLite custom_interviews 表承载（取代旧单文件存储），
本模块保留原函数名与语义，仅把"全局单份"改为"按用户一份"。
"""

import app.db as db


def save_custom_interview(user_id: int, job_title: str, jd: str, questions: list[str]) -> None:
    """保存某用户最新一份定制面试（覆盖旧值）。"""
    db.save_custom_interview(user_id, job_title, jd, questions)


def load_custom_interview(user_id: int) -> dict | None:
    """读取某用户最新定制面试；不存在、损坏或没有题目时返回 None。"""
    return db.load_custom_interview(user_id)


def clear_custom_interview(user_id: int) -> None:
    """清除某用户的定制面试（语音面试完成或用户取消时调用）。"""
    db.clear_custom_interview(user_id)
