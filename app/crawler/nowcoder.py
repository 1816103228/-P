"""牛客网适配器（占位）。

牛客面经/题库反爬较重（登录墙 + 风控），暂不接入。
后续接入思路：
1. 面经接口：https://www.nowcoder.com/discuss/tag/...（需带 cookie）
2. 题库：https://www.nowcoder.com/exam/company/...（登录后可用）
待验证可稳定抓取后再实现 fetch()。
"""

from app.crawler.base import SourceAdapter


class NowCoderAdapter(SourceAdapter):
    """牛客网（占位，未实现）。"""

    name = "nowcoder"

    def fetch(self, limit: int | None = None) -> list[dict]:
        return []
