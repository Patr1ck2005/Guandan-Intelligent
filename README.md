# 掼蛋智能分析引擎 (Guandan Intelligent)

掼蛋游戏引擎与 AI 决策系统，支持人机对战、AI 对战和场景化 benchmark 分析。

## 功能

- **完整游戏引擎** — 掼蛋规则实现，包括牌型识别、出牌合法性判断、升级计分
- **多种 AI 智能体** — 启发式 Agent、蒙特卡洛采样 Agent、ISMCTS Agent、**LLM 大模型 Agent**
- **LLM 端到端决策** — 接入 DeepSeek API，大模型直接观察局面选择出牌（候选模式 / 自由模式）
- **分层评分模型** — Blind / Informed / Round / Exact / Endgame 五层评分体系
- **候选枚举策略** — Full / Memory / Top-N 三种枚举器可配置
- **Web 对战界面** — Flask 驱动的 Web UI，人类玩家可对阵 3 个 AI
- **Arena 测试场** — 预设残局场景，对比不同模型表现
- **对手建模** — 基于已出牌信息推断对手手牌分布
- **Token 成本追踪** — LLM 模式下实时统计 API 调用次数和费用

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Web 服务
python -m guandan.ui.web.app
# 浏览器打开 http://localhost:8765

# 运行测试
pytest
```

### LLM Agent 配置

```bash
# 设置 DeepSeek API Key（永不写入 config.json）
export DEEPSEEK_API_KEY="sk-your-key-here"

# 启动后在前端配置面板中选择 "LLM 大模型" 作为任意玩家的决策器
# 未设置 API Key 时自动降级为启发式 Agent
```

## 规则支持（rule fidelity）

引擎按**江苏竞赛规则**实现：

- 牌型层级：王炸 ＞ 同花顺 ＞ 普通炸弹 ＞ 一般牌型；同花顺之间比点数（不限花色）
- **进贡/还贡/抗贡**（`guandan/tribute.py`）：单下末游进贡、双下双贡；贡最大非级牌（级牌不可进贡）；持一对大王抗贡（全免，头游先出）；收贡方还贡任意一张
- **级牌归属**：下一局级牌 = 上一局头游队伍的级别；先出权按进贡规则（进贡者先出 / 双下大贡先出 / 抗贡头游先出）
- **顺子固定 5 张**（`constants.STRAIGHT_LEN`）：官方制顺子恰好 5 张，6 张以上连张不再是合法牌型；连对（≥3 对）与钢板（≥2 组）保持官方下限
- **确定性与可复现**：自对弈每局使用独立 RNG（发牌 + 首出 + agent 全局随机），同 seed 同结果；`Game(..., rng=...)` 可注入
- 已知简化：两队共用一张级牌（官方制为每队各自级牌，见 roadmap）

## 项目结构

```
guandan/
├── card.py              # 卡牌编码（0-107 整数 ID）
├── combo.py             # 牌型定义（10 种）与比较
├── combo_finder.py      # 从手牌枚举合法出牌候选
├── combo_parser.py      # 牌型输入解析（含万能牌）
├── rules.py             # 出牌合法性校验（RulesEngine）
├── game_state.py        # 游戏状态（frozen dataclass）
├── game.py              # 对局编排（round/trick 循环）
├── table.py             # 桌面状态（当前圈）
├── score.py             # 计分与升级
├── tribute.py           # 进贡/还贡/抗贡（江苏竞赛规则）
├── deck.py              # 牌堆（创建、洗牌、发牌）
├── constants.py         # 游戏常量
├── state_utils.py       # 状态操作工具（clone, apply, next_player）
├── logging.py           # 日志（控制台 + 文件 + 环形缓冲）
├── ai/
│   ├── agent.py         # AI Agent（Heuristic / MonteCarlo / FirstPlay / Random）
│   ├── scorer.py        # 出牌评分入口
│   ├── hand_eval.py     # 手牌质量评估
│   ├── opponent.py      # 对手手牌推断（CardCounter）
│   ├── player_view.py   # 玩家视角状态（屏蔽对手手牌）
│   ├── action_log.py    # 出牌记录 + CardTracker
│   ├── candidate_enum.py# 候选枚举器（Full / TopN / Memory）
│   ├── memory_enumerator.py
│   ├── constrained_sampler.py
│   ├── sampler.py       # 手牌采样（Random / Constrained）
│   ├── registry.py      # 模型注册 + Schema 生成 + 工厂函数
│   ├── schema.py        # SchemaBuilder（前端自动发现）
│   ├── params.py        # AIParams 可配置参数（含 2v2 队友/对手建模权重）
│   ├── ismcts.py        # ISMCTS 树搜索决策器
│   ├── mc_decider.py    # 蒙特卡洛决策器
│   ├── models/
│   │   ├── interface.py     # 评分模型接口（TestableModel）
│   │   ├── blind_scorer.py  # 盲评（只看自己手牌）
│   │   ├── informed_scorer.py # 知情评分（全信息）
│   │   ├── round_scorer.py  # 回合级评分
│   │   └── endgame_solver.py# 残局精确求解（minimax）
│   └── llm/             # NEW: LLM 大模型决策模块
│       ├── config.py    # LLMConfig 配置 + 环境变量读取
│       ├── client.py    # DeepSeek HTTP 客户端（重试 + 流式）
│       ├── prompts.py   # System Prompt + 局面序列化
│       ├── parser.py    # LLM 响应解析 + 校验
│       ├── tracker.py   # Token 用量 + 费用追踪
│       └── agent.py     # LLMAgent（BaseAgent 实现）
├── arena/
│   └── scenarios.py     # Arena 场景库（12 个推理场景）
├── eval/                # Eval Lab（评估实验室）
│   ├── eval_api.py      # 评估任务 API（benchmark/match/RR/tuner/regression）
│   ├── selfplay.py      # 自对弈（多进程可选，per-seed 确定性）
│   ├── scenario_bank.py # 场景题库（策划 + 合成）
│   ├── metrics.py       # 准确率/Wilson 置信区间/agreement/regret
│   ├── elo.py           # Elo 排名（K=32）
│   ├── store.py         # SQLite 历史库（data/eval_history.db）
│   ├── regression.py    # 回归基线套件（红/绿徽章）
│   └── tuner.py         # AIParams 自动调参（随机搜索 + 预算硬上限）
└── ui/
    ├── interactive.py   # 交互式对局接口（step-driven，会话锁 + 结算幂等）
    └── web/
        ├── app.py       # Flask 主应用（Game API + 会话管理）
        ├── arena_api.py # Arena API（Flask Blueprint，模拟器带锁 TTL 池）
        ├── eval_api.py  # Eval Lab API（job 调度 / 历史 / 导出 / tuner）
        ├── templates/
        │   ├── menu.html
        │   ├── game.html
        │   ├── arena.html
        │   └── eval.html
        └── static/
            ├── style.css
            ├── arena.css
            ├── eval.css
            ├── tokens.css
            ├── game.js          # 对战页前端逻辑
            ├── arena.js         # Arena 前端逻辑（自包含，直接 fetch）
            ├── eval.js          # Eval Lab 前端逻辑（自包含，直接 fetch）
            └── lib/
                └── api.js       # 对局页专用 API 客户端（fetch + AbortController + 超时）

tests/
├── test_core.py             # 核心引擎测试（含固定 5 张顺子边界矩阵）
├── test_ai.py               # AI 决策测试
├── test_llm_agent.py        # LLM Agent 模块测试（mock HTTP）
├── test_tribute.py          # 进贡/还贡/抗贡 + 全局集成
├── test_eval.py             # 评估指标/自对弈/题库
├── test_eval_jobs.py        # 任务管理（上限/心跳/停滞/截断）
├── test_eval_store.py       # SQLite 历史库
├── test_eval_history_api.py # 历史 API + 导出
├── test_eval_stabilization.py # Eval 输入校验/调优应用/题库隔离
├── test_selfplay_mp.py      # 多进程自对弈（确定性/崩溃隔离）
├── test_teammate_heuristics.py # 2v2 队友权重
├── test_opp_modeling.py     # 公开信息对手建模
├── test_sampler_seats.py    # 采样器全座位矩阵（防座位硬编码回归）
├── test_regression.py       # 回归基线对比逻辑
├── test_tuner.py            # 自动调参器（硬取消语义）
├── test_interactive_guardrails.py # 对局状态机护栏（幂等结算/超时/锁）
├── test_web_api.py          # Game API 输入健壮性
└── test_arena_stabilization.py    # Arena API 校验/竞态护栏

tools/
├── e2e_ui.js          # Playwright 按钮级 e2e（对战/评估/Arena 全链路）
├── e2e_demo.py        # API 级端到端（基准→入库→导出→回归→调参）
└── visual_audit.js    # 4 页视觉巡检 + 控制台错误扫描

config.json              # AI 模型参数配置（Schema 驱动，gitignored）
CLAUDE.md                # Claude Code 项目文档
```

## 配置

通过 `config.json` 配置 AI 行为，也通过 Web UI 实时修改：

- **decider** — 决策器参数（默认模型: mc，可选: blind / mc / ismcts / llm）
- **inner_model** — 各评分模型的超参（奖励/惩罚权重）
- **enumerator** — 候选枚举策略（full / memory / top_n）
- **llm** — LLM Agent 参数（模型: deepseek-chat / deepseek-reasoner，模式: candidate / free）

## API 端点

### 游戏 API

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/state` | GET | 当前游戏状态 |
| `/api/play` | POST | 出牌 |
| `/api/pass` | POST | 过牌 |
| `/api/start_game` | POST | 开始游戏 |
| `/api/new_game` | POST | 新游戏 |
| `/api/new_round` | POST | 新一局 |
| `/api/hint` | GET | 快速出牌建议 |
| `/api/suggest` | GET | AI 完整建议（含胜率） |
| `/api/evaluate` | POST | 蒙特卡洛评估指定牌型 |
| `/api/debug` | GET | 调试模式（显示全手牌） |
| `/api/config` | GET/POST | 读取/保存 AI 配置 |
| `/api/auto_play` | POST | 切换自动出牌 |
| `/api/log` | GET | 查询日志 |
| `/api/ai_log` | GET | 查询 AI 日志 |

### Arena API

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/arena/models` | GET | 可用 AI 模型列表 |
| `/api/arena/scenarios` | GET | 场景列表（按分类） |
| `/api/arena/scenarios/<id>` | GET | 场景详情 |
| `/api/arena/analyze/scenario` | POST | 模型分析场景 |
| `/api/arena/benchmark` | POST | 批量基准测试 |
| `/api/arena/sim/init` | POST | 从场景初始化模拟器 |
| `/api/arena/sim/init_custom` | POST | 自定义手牌初始化 |
| `/api/arena/sim/state` | POST | 获取模拟器状态 |
| `/api/arena/sim/step` | POST | 执行一步 |
| `/api/arena/sim/analyze` | POST | 分析当前模拟状态 |
| `/api/arena/check_combo` | POST | 校验牌型合法性 |

### Eval Lab API

| 端点 | 方法 | 说明 |
| --- | --- | --- |
| `/api/eval/models` | GET | 可用模型 schema |
| `/api/eval/scenarios` | GET | 场景题库（过滤） |
| `/api/eval/scenarios/generate` | POST | 生成合成场景（精确解标注） |
| `/api/eval/benchmark` | POST | 场景基准（含 Wilson 95% CI） |
| `/api/eval/match` | POST | 自对弈（max_workers>1 走多进程） |
| `/api/eval/round_robin` | POST | 循环赛 Elo |
| `/api/eval/jobs/<id>` | GET | 任务进度（心跳/停滞检测） |
| `/api/eval/jobs/<id>/cancel` | POST | 取消任务 |
| `/api/eval/history` | GET | 历史记录（SQLite 持久化） |
| `/api/eval/history/<id>` | GET | 单条历史详情 |
| `/api/eval/export/<id>` | GET | 报告导出（json/csv/html） |
| `/api/eval/regression` | POST | 回归套件（对比基线，红/绿判定） |
| `/api/eval/regression/latest` | GET | 最近回归结果徽章 |
| `/api/eval/regression/baseline` | POST | 保存当前为基线 |
| `/api/eval/tuner` | POST | AIParams 自动调参（预算硬上限） |
| `/api/eval/tuner/best` | GET | 最近调参最优结果 |
| `/api/eval/tuner/apply` | POST | 将最优权重写入 config.json（返回 diff） |

## 技术栈

- Python 3.11+
- Flask + Gunicorn（Web 服务）
- httpx（LLM API 客户端）
- pytest（测试）
- Vanilla JavaScript（前端，无框架）

## 掼蛋规则简介

掼蛋是一种四人结对扑克游戏，使用两副牌（共 108 张）。支持 10 种牌型：单张、对子、三同张、三带二、顺子、连对、钢板、普通炸弹、同花顺、火箭。每局升级制，先打到 A 的团队获胜。红桃级牌为万能牌，可替代 3-A 的任意牌。

## Roadmap（下一阶段候选，非本阶段承诺）

- 单级牌 → 每队各自级牌（官方制完全体）
- `eval_api.py`（~700 行）按 benchmark / match / history / tuner 拆分为子模块
- 调参器升级：候选间共享评估的贝叶斯优化 / 更大预算的消耗性搜索
- 人类还贡横幅的确定性 e2e 触发（当前依赖随机发牌，已标注 skip）
- ISMCTS 重写为深层树搜索（当前为 1 层 UCB 候选评审，文案已如实标注）
- MonteCarloAgent / MCDecider 双栈合并、CardCounter → CardTracker 收敛
- MC 决策器 pass 真实打分（需先建"应让牌"题库场景）
- Arena 题库扩充 sampling / endgame / opening 类别（当前 12 题全为 deduction）

## 部署约束

- **单进程运行**：`python -m guandan.ui.web.app`（或 启动服务.bat）。所有会话/任务/
  配置锁与内存态均为进程内对象，Gunicorn 多 worker 部署会使它们失效——文档与
  启动脚本均按单进程口径维护。
- `config.json` 由游戏页「AI设置」与 Eval「应用调优权重」两个端点写入，共享
  `_CONFIG_WRITE_LOCK` 并原子替换；文件不入 git，坏写不可 git 恢复。

## 验收工具

```bash
node tools/visual_audit.js   # 4 页视觉检验 + 控制台错误扫描（需 npx playwright install chromium）
node tools/e2e_ui.js         # 按钮级 e2e：整局对战 / 基准 / 历史 / 回归 / Arena 自定义推演
python tools/e2e_demo.py     # API 级端到端：基准→入库→导出→回归→调参
```
