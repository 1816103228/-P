"""兼容入口（UI 已合并至根目录 main.py）。

保留本文件仅为兼容旧的 `streamlit run app/ui/app.py` 启动方式；
实际界面与逻辑统一在根目录 main.py 中实现，避免双份 UI 逻辑分叉。
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]  # 项目根（app/ui/ 的上两级）
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import main  # noqa: F401,E402  （执行主界面）
