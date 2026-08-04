# 面试官小P 🎤

基于 DeepSeek 大模型的 **Python/后端面试模拟与辅导 Agent**。以 Streamlit 聊天界面为载体，结合实时爬取的面试题库，提供**模拟面试**与**辅导答疑**两种模式，帮你为真实面试做好充分准备。

## 功能特性

- **模拟面试模式**：按真实面试流程推进——自我介绍 → 六阶段递进出题（难度由浅入深）→ 每题点评 + 追问深挖 → 结束时输出 0-100 评分总结报告（技术正确性 40% / 表达清晰度 30% / 逻辑深度 20% / 项目经验表述 10%）。
- **辅导答疑模式**：直接提问，小P 给出"标准参考回答 + 加分点 + 变式题"三段式解答，并自动检索本地题库辅助回答（FTS5 全文检索 RAG）。
- **定制面试**：开始前输入**目标岗位 + 招聘信息（JD）**，小P 据此生成一套专属面试题（由浅入深，贴近 JD 技术点）再开始面试。
- **实时题库**：自动爬取面试鸭（mianshiya.com）与 LeetCode（力扣中国站）题库，SQLite 本地存储，按内容哈希增量去重。
- **定时更新**：APScheduler 调度——启动即抓一次、每天固定时间抓取、按间隔小时抓取，配合**单实例文件锁**防止多进程重复抓取。
- **流式输出**：回答逐字渲染（`st.write_stream`），不用再等整段回复，体验更流畅。
- **LLM 调用加固**：指数退避重试 + 超时控制 + 请求级日志（耗时/用量），网络抖动不再直接报错。
- **上下文管理**：对话超出阈值自动把早期内容压缩成摘要，控制 token 成本与延迟。
- **题库浏览**：按来源 / 难度 / 关键词筛选，一键「出这道题」直接进入模拟面试。
- **语音通话**：像打电话一样的实时对话——持续收音、识别到一句话就发、小P 边生成边播报（edge-tts 神经语音），不用等把话说完才得到回应。
- **开口即打断**：播报时你用正常音量说话即可打断（麦克风带回声抑制 + 自适应回声基准，不会被小P 自己的声音误触发）。
- **随时挂断**：右下角按钮始终是**挂断**（会立即停止正在播报的声音）；接通后说话打断，点按钮挂断。
- **健壮性**：LeetCode 接口失败自动降级用本地缓存、FTS5 不可用回退 LIKE、SQLite 开启 WAL 与 busy_timeout、日志轮转。

## 技术栈

| 类别 | 技术 |
|------|------|
| 前端界面 | Streamlit（含流式输出） |
| 大模型 | DeepSeek（OpenAI 兼容 SDK） |
| 数据存储 | SQLite（WAL + FTS5 全文索引） |
| 爬虫 | requests（Session 复用 + 重试）+ BeautifulSoup + lxml |
| 定时任务 | APScheduler（单实例锁） |
| 测试 | unittest（标准库） |

## 项目结构

```
智能面试/
├── main.py                    # 主入口：Streamlit 聊天界面（语音通话、虚拟人物、题库浏览）
├── voice_server.py            # 语音通话服务（FastAPI + WebSocket，全双工对话）
├── 启动.bat                    # Windows 一键启动脚本（装依赖 → 选端口 → 开浏览器）
├── requirements.txt           # Python 依赖（带上限约束）
├── pyproject.toml             # 项目元数据与依赖声明
├── Dockerfile                 # 容器化部署
├── docker-compose.yml         # Web + 语音服务编排
├── .github/workflows/ci.yml   # GitHub Actions 测试流水线
├── .env.example               # 环境变量模板（复制为 .env 后填写）
├── app/
│   ├── config.py              # 全局配置：.env 加载、路径与抓取/LLM/DB 参数
│   ├── prompts.py             # 角色设定、流程规则、六阶段配置（单一来源）
│   ├── db.py                  # SQLite 数据层：批量写入、去重、FTS5、版本迁移
│   ├── scheduler.py           # APScheduler 定时爬取（单实例锁 + 日志轮转）
│   ├── agent/
│   │   ├── coach.py           # 会话状态机：双模式 + 上下文压缩 + 流式输出
│   │   └── llm.py             # DeepSeek 封装：重试、超时、流式、结构化输出
│   ├── crawler/
│   │   ├── base.py            # SourceAdapter 基类 + 共享 Session 工厂
│   │   ├── mianshiya.py       # 面试鸭适配器（并行抓取）
│   │   ├── leetcode.py        # LeetCode 适配器（本地缓存 + 降级）
│   │   ├── nowcoder.py        # 牛客适配器（占位，暂未接入）
│   │   ├── classify.py        # DeepSeek 批量打标签（json_object + 失败重试）
│   │   └── run.py             # 汇总抓取入口
│   ├── ui/
│   │   ├── components.py      # 共享 UI 组件：虚拟人物 / 侧边栏 / 题库浏览
│   │   ├── voice_script.js    # 语音通话前端逻辑（识别/播放/打断/降级）
│   │   └── app.py             # 兼容入口（统一指向 main.py）
│   └── tests/                 # 单元测试（数据层、爬虫、LLM、教练状态机、UI）
├── data/
│   ├── questions.db           # SQLite 题库（当前约 3700+ 题）
│   ├── scheduler.log          # 爬虫调度日志（自动轮转）
│   ├── leetcode_cache.json    # LeetCode 响应缓存（自动生成）
│   ├── scheduler.lock         # 调度器单实例锁（自动生成）
│   └── probe_mianshiya.html   # 面试鸭页面结构探测文件
└── .reasonix/
    └── skills/interview-coach/  # Reasonix 技能配置（角色说明）
```

## 快速开始

### 环境要求

- Python 3.10+
- 一个 DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com) 获取）

### 安装与配置

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 创建环境变量文件并填写密钥
copy .env.example .env
```

编辑 `.env`，至少配置：

```ini
DEEPSEEK_API_KEY=sk-你的key
```

> ⚠️ `.env` 已被 `.gitignore` 排除，请勿将真实密钥提交到版本控制或公开场合。

### 启动

**方式一（Windows 推荐）**：双击项目根目录的 `启动.bat`，脚本会自动安装依赖、检测端口（8501 被占用时改用 8502）、**同时启动语音通话服务（8765）**并打开浏览器。

**方式二（命令行）**：

```bash
# 终端 1：语音通话服务
python -m uvicorn voice_server:app --host 127.0.0.1 --port 8765

# 终端 2：Web 界面
streamlit run main.py
```

然后访问 <http://localhost:8501>。

**方式三（Docker）**：

```bash
docker compose up -d
```

## 语音通话（像打电话一样）

点击右下角 **📞 按钮接通**，开始实时语音对话：

- **边说边答**：浏览器用 Web Speech API 持续识别（zh-CN），每识别出一句话立刻发给语音服务；DeepSeek 流式回复逐字推回，浏览器按句子边界即时播报，不等整段生成完。
- **自然甜美音色**：服务端用 edge-tts 神经语音（默认微软晓晓 zh-CN-XiaoxiaoNeural）逐句合成，语速/音调可配置；edge-tts 不可用时自动降级为浏览器本地语音（自动挑选最自然的中文音色）。
- **开口即打断**：播报期间识别会暂停（防止回声自激），改用**带回声抑制的麦克风音量检测（VAD）**——你正常说话就会打断播报并取消未完成的生成；点按钮同样可以打断。
- **自动恢复**：一句话回答结束后自动回到聆听状态。
- **双模式**：接通后直接提问走**辅导答疑**；接通后说"开始面试 / 模拟面试"，自动切换为**模拟面试**（小P 出题 → 你回答 → 点评追问）。
- **通话不断线**：语音状态挂在浏览器父窗口，切换模式、发文字消息等页面刷新不会挂断通话。
- **环境要求**：语音识别依赖浏览器 Web Speech API，请使用 Chrome 或 Edge；需先启动 `voice_server`（`启动.bat` 会自动启动）。

通话链路：浏览器（识别/播放/打断）⇄ WebSocket ⇄ `voice_server`（会话状态机 + DeepSeek 流式回复 + edge-tts 合成）。

### 定制面试（目标岗位 + JD）

开始前展开 **🎯 定制面试**，输入目标岗位（如"高级 Python 后端工程师"）并粘贴招聘信息/JD，点击"生成定制面试题并开始"。小P 会：

1. 根据 JD 中的技术栈生成一套递进式面试题（基础 → 核心 → 项目/场景设计）；
2. 进入模拟面试模式，逐题提问、点评、追问；
3. 全部答完后照常输出评分报告。

不填写 JD 也可以只输入岗位名，会按通用岗位出题。

## 使用说明

### 模式一：模拟面试

点击「开始面试」，小P 按以下六个阶段逐题推进（前期偏简单、后期偏难）：

| 阶段 | 考察范围 | 期望难度 |
|------|----------|----------|
| Python 基础 | 语言特性、GIL、装饰器、生成器、内存管理等 | 简单 |
| 数据结构与算法 | 排序、动态规划、双指针、树图、贪心、二分、回溯等 | 中等 |
| 数据库 / SQL | SQL 基础、索引、事务 ACID、Redis 等 | 中等 |
| 网络与并发 | HTTP/TCP、操作系统、多线程 / 协程等 | 中等 |
| 项目深挖 | STAR 法则、技术难点、设计模式、框架工具 | 困难 |
| 场景设计题 | 系统设计、缓存策略、接口设计 | 困难 |

每答完一题，小P 先点评（优点 + 不足），再追问一个深挖细节；全部结束后输出评分报告与改进建议清单。

### 模式二：辅导答疑

直接在输入框提问（如"Redis 缓存穿透怎么答"），小P 会检索本地题库（FTS5 全文搜索标题/题干/答案/标签）辅助作答，并给出标准参考回答、加分点，以及一道同类变式题供你练习。

## 题库爬取与更新

### 手动抓取

```bash
# 全量抓取（面试鸭 + LeetCode）
python -m app.crawler.run

# 调试：每个来源最多抓取 N 条
python -m app.crawler.run --limit 50
```

### 定时抓取

调度策略（`app/scheduler.py`）：

1. 应用启动后立即在后台抓取一次（新装即有题库）；
2. 每天 `CRAWL_TIME`（默认 02:00）抓取一次；
3. 按 `CRAWL_INTERVAL_HOURS`（默认 24 小时）间隔抓取。

重复抓取由 `content_hash`（题目内容归一化 SHA-256）保证增量去重。**单实例文件锁**确保多个进程同时启动时只有第一个进程跑调度器。抓取日志写入 `data/scheduler.log`（5MB 自动轮转，保留 3 份）。

### 打标签

```bash
# 给未分类题目打标签（json_object 输出，解析失败自动重试）
python -m app.crawler.classify

# 预览不写库
python -m app.crawler.classify --dry

# 只处理前 50 条
python -m app.crawler.classify --limit 50
```

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DEEPSEEK_API_KEY` | 无 | DeepSeek API 密钥（**必填**） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 地址（OpenAI 兼容） |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 使用的模型名 |
| `LLM_TIMEOUT_SECONDS` | `120` | LLM 请求超时（秒） |
| `LLM_MAX_RETRIES` | `3` | LLM 失败重试次数（指数退避） |
| `CRAWL_TIME` | `02:00` | 每日定时抓取时间 |
| `CRAWL_INTERVAL_HOURS` | `24` | 间隔抓取小时数，`0` 表示关闭间隔抓取 |
| `SCHEDULER_TZ` | `Asia/Shanghai` | 调度器时区 |
| `CRAWL_PAGES_PER_CATEGORY` | `10` | 面试鸭每个分类抓取页数 |
| `CRAWL_REQUEST_DELAY` | `0.3` | 面试鸭页间请求间隔（秒） |
| `CRAWL_WORKERS` | `3` | 面试鸭分类并行线程数 |
| `LEETCODE_CACHE_HOURS` | `72` | LeetCode 响应缓存有效期（小时） |
| `DB_TIMEOUT_SECONDS` | `10` | SQLite busy_timeout（秒） |
| `VOICE_TTS` | `edge` | 语音合成方式：`edge`=在线神经语音，`local`=浏览器本地语音 |
| `VOICE_NAME` | `zh-CN-XiaoxiaoNeural` | edge-tts 音色（微软晓晓，甜美自然） |
| `VOICE_RATE` | `+0%` | 播报语速（如 `-10%` 更慢） |
| `VOICE_PITCH` | `+2Hz` | 播报音调（如 `+5Hz` 更细更高） |
| `VOICE_VAD_THRESHOLD` | `0.045` | 开口打断的音量阈值（0~1，回声抑制已滤除扬声器声音） |
| `DISABLE_SCHEDULER` | 未设置 | 设为 `1` 可禁用后台爬取（测试/多实例部署用） |

## 运行测试

```bash
# 全量测试（数据层 / 爬虫 / LLM 重试 / 教练状态机 / 启动链路 / UI 渲染）
python -m unittest discover -s app/tests -v
```

测试默认禁用调度器（`DISABLE_SCHEDULER=1`），LLM 与网络请求全部 mock，不触网、不消耗 API 额度。CI（GitHub Actions）会在 Python 3.10 / 3.11 上自动执行。

## 数据库说明

- 连接管理：每次操作显式关闭连接，并开启 `busy_timeout` / `synchronous=NORMAL`，避免多线程读写锁冲突。
- 批量写入：`upsert_many` 使用单事务 `executemany`，全量抓取耗时从约 3.5 分钟降到数秒级。
- 全文检索：FTS5 外部内容表覆盖标题/题干/答案/标签，按 `bm25` 相关性排序；FTS5 不可用时自动回退 LIKE。
- 版本迁移：通过 `PRAGMA user_version` 管理，后续加表/加字段只需在 `db._migrate` 追加迁移步骤。

## 常见问题

**1. 启动时报"未配置 DEEPSEEK_API_KEY"**  
确认已复制 `.env.example` 为 `.env` 并填入真实密钥。

**2. 题库为空，模拟面试无题可出**  
先运行 `python -m app.crawler.run` 抓取题库，或检查网络与 `data/scheduler.log` 日志。

**3. LeetCode 抓取报 403**  
无需处理：接口失败会自动重试，仍失败则降级使用本地缓存（`data/leetcode_cache.json`，有效期默认 72 小时），不会让整个爬取链路中断。

**4. 8501 端口被占用**  
`启动.bat` 会自动改用 8502，也可手动指定端口启动。

**5. 语音输入不工作**  
请使用 Chrome 或 Edge 浏览器，并确认语音服务已启动：访问 <http://127.0.0.1:8765/health> 应返回 `{"status":"ok"}`（`启动.bat` 会自动启动）。接通后状态条会显示"聆听中 / 小P思考中 / 播报中"。

**6. 多个实例同时启动会重复抓取吗**  
不会。`data/scheduler.lock` 单实例锁保证只有第一个进程运行调度器；部署多实例时建议只让一个实例启用调度（其余设 `DISABLE_SCHEDULER=1`）。

## 数据源说明

- **面试鸭（mianshiya.com）**：Python / 后端 / 数据库 / 计算机网络 / 操作系统 / 算法 / 项目 7 个分类，分类间并行抓取，页间礼貌限速。
- **LeetCode（力扣中国站）**：官方公开 API 抓取全部免费题目，跳过会员题，响应本地缓存。
- **牛客网**：反爬较重（登录墙 + 风控），当前为占位适配器，待验证稳定抓取后再接入。

如需新增数据源，继承 `app/crawler/base.py` 中的 `SourceAdapter`，实现 `fetch()`，并在 `app/crawler/run.py` 的 `ADAPTERS` 列表登记即可。
