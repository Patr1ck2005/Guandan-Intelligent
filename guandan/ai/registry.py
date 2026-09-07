"""Unified AI model registry with schema-driven configuration.

All model definitions are here. SchemaBuilder generates JSON for the frontend.
Add a new model here → frontend auto-picks it up. Zero JS changes.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .agent import BlindAgent
from .params import AIParams, DEFAULT_PARAMS as DEFA
from .schema import SchemaBuilder, ModelDef, Param
from .sampler import create_sampler


# ==================================================================
# Schema builder — models register themselves here
# ==================================================================

_builder = SchemaBuilder()

# --- Category: decider (outer layer, what players choose) ---
_dec = _builder.category("decider", "决策器", "玩家直接使用的决策模型")

_blind = _builder.model("decider", "blind", "Blind 盲评",
    description="只看自己手牌，按AIParams权重打分")
# Only genuine float-tunable AIParams fields belong here: log_level/log_*
# leaked in via to_dict() and showed up as fake "float" tuning knobs.
for k, v in DEFA.to_dict().items():
    if k.startswith("log_"):
        continue
    _builder.param(_blind, k, "float", v, label=k, step=0.5)

_mc = _builder.model("decider", "mc", "MC 蒙特卡洛",
    description="采样unseen→内层模型模拟→胜率投票", is_mc=True)
_builder.param(_mc, "num_samples", "int", 128, label="采样次数")
_builder.param(_mc, "time_limit_ms", "int", 10000, label="时限(ms)")
_builder.param(_mc, "sampler", "select", "random", label="采样器",
               options={"random": "RandomSampler", "constrained": "ConstrainedSampler"})
_builder.param(_mc, "inner", "ref", "informed", label="内层模型", ref_category="inner_model")
_builder.param(_mc, "enumerator", "ref", "top_n", label="候选枚举", ref_category="enumerator")

# IS-MCTS
_ism = _builder.model("decider", "ismcts", "IS-MCTS 树搜索",
    description="UCB 候选评审：对候选逐个胜率模拟，按访问次数选优（当前实现为 1 层 UCB，不做深层树展开）", is_mc=True)
_builder.param(_ism, "max_iterations", "int", 500, label="最大迭代")
_builder.param(_ism, "time_limit_ms", "int", 10000, label="时限(ms)")
_builder.param(_ism, "ucb_c", "float", 1.4, label="UCB探索常数", step=0.1)
_builder.param(_ism, "sampler", "select", "random", label="采样器",
               options={"random": "RandomSampler", "constrained": "ConstrainedSampler"})
_builder.param(_ism, "inner", "ref", "informed", label="内层模型", ref_category="inner_model")
_builder.param(_ism, "enumerator", "ref", "full", label="候选枚举", ref_category="enumerator")

# LLM
_llm = _builder.model("decider", "llm", "LLM 大模型",
    description="DeepSeek API 端到端决策。API Key 从 DEEPSEEK_API_KEY 环境变量读取。")
_builder.param(_llm, "model", "select", "deepseek-reasoner", label="模型",
               options={"deepseek-chat": "DeepSeek V3", "deepseek-reasoner": "DeepSeek R1 (思考模式)"})
_builder.param(_llm, "mode", "select", "candidate", label="模式",
               options={"candidate": "候选模式(LLM选候选)", "free": "自由模式(LLM直接选牌)"})
_builder.param(_llm, "candidate_order", "select", "natural", label="候选排序",
               options={"natural": "自然顺序", "by_length_desc": "多牌优先"})
_builder.param(_llm, "max_candidates", "int", 200, label="最大候选数")
_builder.param(_llm, "temperature", "float", 0.6, label="温度", step=0.05)
_builder.param(_llm, "max_tokens", "int", 1024, label="最大Token")
_builder.param(_llm, "timeout_s", "float", 45.0, label="超时(秒)", step=1.0)
_builder.param(_llm, "max_retries", "int", 2, label="最大重试")
_builder.param(_llm, "fallback_agent", "select", "heuristic", label="降级策略",
               options={"heuristic": "HeuristicAgent", "mc": "MonteCarloAgent", "pass": "直接过牌"})

# --- Category: inner_model (MC simulation internal) ---
_inn = _builder.category("inner_model", "内层模型", "MC模拟内部使用，需完全信息")

_i_blind = _builder.model("inner_model", "blind", "Blind 盲评",
    description="与外部Blind相同，模拟内每步评分", needs_full_info=True)
for k, v in DEFA.to_dict().items():
    if k.startswith("log_"):
        continue
    _builder.param(_i_blind, k, "float", v, label=k, step=0.5)

_i_inf = _builder.model("inner_model", "informed", "Informed 控权评分",
    description="利用已知四家手牌，按控权权重打分", needs_full_info=True)
for k, v in {"round_weight":5.0,"no_counter_bonus":8.0,"teammate_cover_bonus":2.0,
             "opponent_counter_penalty":3.0,"bomb_lead_penalty":5.0,
             "bomb_overuse_penalty":4.0,"pass_teammate_bonus":3.0,
             "pass_control_bonus":2.0,"pass_neutral":0.0}.items():
    _builder.param(_i_inf, k, "float", v, label=k, step=0.5)

_i_rnd = _builder.model("inner_model", "round", "Round 轮次评分",
    description="基于estimate_rounds的精确轮次计算", needs_full_info=True)
for k, v in {"round_delta_weight":8.0,"gap_improve_weight":3.0,"no_counter_bonus":6.0,
             "teammate_cover_bonus":2.0,"opponent_counter_penalty":2.0,
             "pass_teammate_bonus":4.0,"pass_default":1.0}.items():
    _builder.param(_i_rnd, k, "float", v, label=k, step=0.5)

_i_ex = _builder.model("inner_model", "exact", "Exact 精确求解",
    description="≤6牌minimax穷举，无权重纯搜索", needs_full_info=True)
_builder.param(_i_ex, "max_depth", "int", 20, label="最大深度")
_builder.param(_i_ex, "max_cards", "int", 6, label="最大手牌数")

# --- Category: enumerator ---
_enu = _builder.category("enumerator", "候选枚举", "MC候选生成策略")

_full = _builder.model("enumerator", "full", "全面枚举",
    description="穷举所有同类型牌型+炸弹")

_mem = _builder.model("enumerator", "memory", "记忆感知枚举",
    description="根据过牌历史调整候选顺序")

_topn = _builder.model("enumerator", "top_n", "预筛TopN",
    description="预评分后取前N个候选")
_builder.param(_topn, "top_n", "int", 5, label="保留数")
# Pre-scoring must stay blind-only: informed/round would read all four real
# hands before opponent sampling, handing the outer MC perfect information.
_builder.param(_topn, "pre_scorer", "select", "blind", label="预评分器",
               options={"blind": "Blind"})


# ==================================================================
# Schema query
# ==================================================================

def get_schema() -> dict:
    return _builder.to_json()


# ==================================================================
# Default config (for new games)
# ==================================================================

DEFAULT_CONFIG = {
    "players": {p: {"decider": None, "params": {}} for p in range(4)},
    "decider": {
        "default_model": "mc",
        "model_params": {
            mid: {k: p.default for k, p in m.params.items()}
            for mid, m in _dec.models.items()
        },
    },
    "inner_model": {
        "model_params": {
            mid: {k: p.default for k, p in m.params.items()}
            for mid, m in _inn.models.items()
        },
    },
    "enumerator": {
        "model_params": {
            mid: {k: p.default for k, p in m.params.items()}
            for mid, m in _enu.models.items()
        },
    },
}


# ==================================================================
# Factory
# ==================================================================

def _make_inner_agent(inner_id: str, params: dict):
    params = _strip_bad_params(params)
    if inner_id == "informed":
        from .models.informed_scorer import InformedScorer
        return InformedScorer(**params)
    elif inner_id == "round":
        from .models.round_scorer import RoundScorer
        return RoundScorer(**params)
    elif inner_id == "exact":
        from .models.endgame_solver import EndgameExactSolver
        return EndgameExactSolver(**params)
    else:  # blind
        ai_fields = set(AIParams.__dataclass_fields__.keys())
        p = {k: v for k, v in params.items() if k in ai_fields}
        return BlindAgent(params=AIParams.from_dict(p) if p else DEFA)


def _strip_bad_params(params: dict) -> dict:
    """Drop null / non-finite values that sneak in through saved configs
    (the frontend's empty number inputs serialize as null)."""
    out = {}
    for k, v in (params or {}).items():
        if v is None:
            continue
        if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
            continue
        out[k] = v
    return out


def _to_int(params: dict, key: str, dflt: int) -> int:
    try:
        return int(params.get(key, dflt))
    except (TypeError, ValueError):
        return dflt


def _to_float(params: dict, key: str, dflt: float) -> float:
    try:
        return float(params.get(key, dflt))
    except (TypeError, ValueError):
        return dflt


def create_agent_for_player(config: dict, player_id: int):
    """Create outer agent from config."""
    player_cfg = config.get("players", {}).get(player_id, {})
    decider_id = player_cfg.get("decider") or config.get("decider", {}).get("default_model", "mc")
    params = _strip_bad_params(player_cfg.get("params") or {})
    # Merge with defaults
    defaults = config.get("decider", {}).get("model_params", {}).get(decider_id, {})
    params = {**_strip_bad_params(defaults), **params}

    if decider_id in ("mc", "ismcts"):
        sampler_id = params.get("sampler", "random")
        inner_id = params.get("inner", "informed")
        enumerator_id = params.get("enumerator", "top_n")

        inner_params = config.get("inner_model", {}).get("model_params", {}).get(inner_id, {})
        inner = _make_inner_agent(inner_id, inner_params)
        sampler = create_sampler(sampler_id)

        from .candidate_enum import create_enumerator, list_enumerators
        enum_kw = {}
        if enumerator_id == "top_n":
            enum_kw["top_n"] = _to_int(params, "top_n", 5)
            enum_kw["pre_scorer"] = params.get("pre_scorer", "blind")
        if enumerator_id not in list_enumerators():
            # A stale/typo'd enumerator id used to silently mean "full";
            # log it loudly and fall back so live games stay playable.
            from ..logging import ai_log
            ai_log(player_id, "config_warning",
                   message=f"unknown enumerator {enumerator_id!r}, using full")
            enumerator_id = "full"
        enumerator = create_enumerator(enumerator_id, **enum_kw)

        if decider_id == "ismcts":
            from .ismcts import ISMCTSDecider
            return ISMCTSDecider(
                sampler=sampler, inner=inner, enumerator=enumerator,
                max_iterations=_to_int(params, "max_iterations", 500),
                time_limit_ms=_to_int(params, "time_limit_ms", 10000),
                ucb_c=_to_float(params, "ucb_c", 1.4),
                config=config)

        from .mc_decider import MCDecider
        return MCDecider(
            sampler=sampler, inner=inner, enumerator=enumerator,
            num_samples=max(1, _to_int(params, "num_samples", 128)),
            time_limit_ms=_to_int(params, "time_limit_ms", 10000),
            config=config)
    elif decider_id == "llm":
        from .llm import LLMAgent, LLMConfig
        llm_fields = {f.name for f in LLMConfig.__dataclass_fields__.values()}
        llm_params = {k: v for k, v in params.items()
                      if k in llm_fields and v is not None}
        return LLMAgent(config=LLMConfig(**llm_params))
    else:
        return _make_inner_agent("blind", params)


def create_simulation_agent(config: dict, player_id: int = None):
    """Create inner simulation agent for MC rollouts."""
    decider_id = (config.get("players", {}).get(player_id, {}).get("decider")
                  if player_id is not None else None)
    decider_id = decider_id or config.get("decider", {}).get("default_model", "mc")

    if decider_id == "mc":
        defaults = config.get("decider", {}).get("model_params", {}).get("mc", {})
        inner_id = defaults.get("inner", "informed")
    else:
        inner_id = "blind"

    inner_params = config.get("inner_model", {}).get("model_params", {}).get(inner_id, {})
    return _make_inner_agent(inner_id, inner_params)
