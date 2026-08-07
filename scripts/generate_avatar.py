"""用阿里云通义万相生成面试官头像（脚本，需本机可访问外网）。

用法：
    python scripts/generate_avatar.py            # 输出到 app/ui/assets/avatar.png
    python scripts/generate_avatar.py --out D:/tmp/avatar.png

密钥从项目 .env 读取（DASHSCOPE_API_KEY）。模型按 wan2.6 同步 → wan2.2 异步 → wanx-v1 异步
依次尝试，任意一个成功即保存并退出。
"""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402  加载 .env（DASHSCOPE_API_KEY）

BASE = "https://dashscope.aliyuncs.com"

PROMPT = (
    "职业形象照：一位年轻美丽的中国女性，22 岁左右，黑色柔顺长发，大眼睛灵动有神，"
    "五官精致立体，甜美温柔的笑容，清透精致的淡妆，皮肤白皙细腻，气质出众，"
    "身穿浅粉色职业衬衫，端坐半身像，正面看向镜头。背景为干净柔和的浅色渐变工作室背景，"
    "柔和均匀的棚拍灯光，商业人像摄影风格，杂志封面级颜值，高清。"
)
NEGATIVE = (
    "全身，低分辨率，模糊，畸形五官，多余的手指或肢体，文字，水印，logo，"
    "过度磨皮，蜡像感，AI 感，显老，发际线后退，杂乱背景"
)


def _headers(key: str, async_task: bool = False) -> dict:
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_task:
        h["X-DashScope-Async"] = "enable"
    return h


def _save(url: str, out: Path) -> bool:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(r.content)
    print(f"已保存: {out}（{len(r.content)//1024} KB）")
    return True


def _sync_wan26(key: str, out: Path) -> bool:
    """wan2.6-t2i：一次请求直接出图。"""
    url = f"{BASE}/api/v1/services/aigc/multimodal-generation/generation"
    payload = {
        "model": "wan2.6-t2i",
        "input": {"messages": [{"role": "user", "content": [{"text": PROMPT}]}]},
        "parameters": {
            "prompt_extend": True,
            "watermark": False,
            "n": 1,
            "negative_prompt": NEGATIVE,
            "size": "1280*1280",
        },
    }
    r = requests.post(url, headers=_headers(key), json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    try:
        img = data["output"]["choices"][0]["message"]["content"][0]["image"]
    except (KeyError, IndexError, TypeError) as e:
        print(f"wan2.6 响应解析失败: {e} {str(data)[:200]}")
        return False
    return _save(img, out)


def _async_wan(key: str, model: str, out: Path) -> bool:
    """旧版协议：创建任务 → 轮询任务结果（wan2.2 / wanx-v1 等）。"""
    create_url = f"{BASE}/api/v1/services/aigc/text2image/image-synthesis"
    payload = {
        "model": model,
        "input": {"prompt": PROMPT, "negative_prompt": NEGATIVE},
        "parameters": {"size": "1024*1024", "n": 1},
    }
    r = requests.post(create_url, headers=_headers(key, async_task=True), json=payload, timeout=60)
    r.raise_for_status()
    task_id = r.json()["output"]["task_id"]
    print(f"{model} 任务已创建: {task_id}")
    poll_url = f"{BASE}/api/v1/tasks/{task_id}"
    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(10)
        q = requests.get(poll_url, headers=_headers(key), timeout=60)
        q.raise_for_status()
        data = q.json()
        status = data.get("output", {}).get("task_status", "UNKNOWN")
        print(f"  任务状态: {status}")
        if status == "SUCCEEDED":
            for item in data["output"].get("results", []):
                if item.get("url"):
                    return _save(item["url"], out)
            return False
        if status in ("FAILED", "CANCELED"):
            print(f"  任务失败: {data}")
            return False
    print("任务超时")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="生成面试官头像")
    parser.add_argument("--out", default=str(ROOT / "app" / "ui" / "assets" / "avatar.png"))
    args = parser.parse_args()
    key = (config.DASHSCOPE_API_KEY or "").strip()
    if not key:
        print("错误：未找到 DASHSCOPE_API_KEY，请检查 .env")
        return 1
    out = Path(args.out)
    attempts = [
        ("wan2.6-t2i（同步）", lambda: _sync_wan26(key, out)),
        ("wan2.2-t2i-flash（异步）", lambda: _async_wan(key, "wan2.2-t2i-flash", out)),
        ("wanx-v1（异步）", lambda: _async_wan(key, "wanx-v1", out)),
    ]
    for name, fn in attempts:
        print(f"尝试 {name} …")
        try:
            if fn():
                return 0
        except Exception as e:
            print(f"{name} 失败: {e}")
    print("所有模型均失败，请检查 API Key、网络与模型开通状态")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
