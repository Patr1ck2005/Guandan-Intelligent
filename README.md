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
│   ├── params.py        # AIParams 可配置参数
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
└── ui/
    ├── interactive.py   # 交互式对局接口（step-driven）
    └── web/
        ├── app.py       # Flask 主应用（Game API）
        ├── arena_api.py # Arena API（Flask Blueprint）
        ├── templates/
        │   ├── menu.html
        │   ├── game.html
        │   └── arena.html
        └── static/
            ├── style.css
            ├── arena.css
            ├── tokens.css
            ├── game.js          # 主游戏前端逻辑 (~1037 行)
            ├── arena.js         # Arena 前端逻辑
            └── lib/
                ├── api.js       # API 客户端封装（fetch + AbortController）
                ├── card.js      # 卡牌渲染工具
                ├── constants.js # 前端常量
                └── store.js     # 状态存储

tests/
├── test_core.py         # 核心引擎测试
├── test_ai.py           # AI 决策测试
├── test_llm_agent.py    # LLM Agent 模块测试（20 项）
└── arena/
    └── scenarios.py     # 场景定义测试

config.json              # AI 模型参数配置（Schema 驱动）
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

## 技术栈

- Python 3.11+
- Flask + Gunicorn（Web 服务）
- httpx（LLM API 客户端）
- pytest（测试）
- Vanilla JavaScript（前端，无框架）

## 掼蛋规则简介

掼蛋是一种四人结对扑克游戏，使用两副牌（共 108 张）。支持 10 种牌型：单张、对子、三同张、三带二、顺子、连对、钢板、普通炸弹、同花顺、火箭。每局升级制，先打到 A 的团队获胜。红桃级牌为万能牌，可替代 3-A 的任意牌。
