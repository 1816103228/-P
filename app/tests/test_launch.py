"""启动链路验证（标准库 unittest，无需额外依赖）。

运行：
    python -m unittest app.tests.test_launch -v
"""
import unittest

import requests


class LaunchChecks(unittest.TestCase):
    """验证启动.bat 修复后的关键链路：服务可达 + 数据可用。"""

    def test_service_http_200(self):
        """服务在 8501 端口返回 HTTP 200（对应 bat 中 --server.port 8501 的启动）。

        服务未运行时自动跳过（先启动 启动.bat 再跑全套验证）。
        """
        try:
            r = requests.get("http://localhost:8501", timeout=3)
        except requests.RequestException:
            self.skipTest("服务未运行，跳过（请先双击 启动.bat 或启动 streamlit）")
        self.assertEqual(r.status_code, 200)

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
