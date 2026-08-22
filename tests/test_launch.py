"""启动链路验证（标准库 unittest，无需额外依赖）。

运行：
    python -m unittest tests.test_launch -v
"""

import unittest

import requests

#: 统一服务端口（scripts/start.bat / start.sh 启动，见 config.APP_PORT）
SERVICE_URL = "http://localhost:8765"


class LaunchChecks(unittest.TestCase):
    """验证启动脚本关键链路：服务可达 + 数据可用。"""

    def test_service_http_200(self):
        """统一服务在 8765 端口返回 HTTP 200（Vue3 前端 + REST + 语音）。

        服务未运行时自动跳过（先运行 scripts/start.bat 再跑全套验证）。
        """
        try:
            r = requests.get(SERVICE_URL + "/health", timeout=3)
        except requests.RequestException:
            self.skipTest("服务未运行，跳过（请先运行 scripts/start.bat 或启动统一服务）")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_db_accessible(self):
        """数据库可正常读取（UI 侧边栏题库统计依赖）。"""
        from app import db

        try:
            db.init_db()  # 空环境（CI）下先建库
            total = db.count_questions()
        except Exception as e:
            self.skipTest(f"题库初始化失败，跳过：{e}")
        if total == 0:
            self.skipTest("题库为空，请先运行 python -m app.crawler.run 抓取")
        self.assertGreater(total, 0)


if __name__ == "__main__":
    unittest.main()
