"""Static template + rules-text assertions (round-3 interaction audit).

The menu page's rules modal is the only place a player reads the combo
rules — when it drifts from the engine, users attempt plays the validator
rejects (round-3 audit H3: the modal documented 火箭 = 大王+小王 while the
engine requires 4 jokers).
"""

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "guandan" / "ui" / "web" / "templates"


def test_menu_rules_rocket_matches_engine():
    """火箭 = 4 张王（2 大王 + 2 小王）；大小王一对不是合法牌型。"""
    html = (TEMPLATE_DIR / "menu.html").read_text(encoding="utf-8")
    assert "4张王" in html, "rules modal must state rocket = 4 jokers"
    assert "2大王" in html and "2小王" in html
    assert "大小王一对不是合法牌型" in html
    # The old wrong phrasing must not come back
    assert "大王 + 小王，最大牌型" not in html


def test_engine_rocket_is_four_jokers():
    """Pin the engine behavior the modal documents."""
    from guandan.card import Card
    from guandan.combo_parser import ComboParser

    parser = ComboParser(2)
    # one big + one small joker — NOT a legal combo
    assert parser.parse([Card.from_id(52), Card.from_id(53)]) is None
    # all four jokers — rocket
    combo = parser.parse(
        [Card.from_id(52), Card.from_id(53), Card.from_id(106), Card.from_id(107)])
    assert combo is not None
    assert combo.combo_type.name == "ROCKET"


def test_game_help_shortcuts_match_keybindings():
    """The help modal documents Enter 出牌 / Space 过牌 / Ctrl+D 调试 —
    keep it in sync with game.js's keydown handler."""
    html = (TEMPLATE_DIR / "menu.html").read_text(encoding="utf-8")
    assert "Enter" in html and "Space" in html and "Ctrl+D" in html
    game_js = (TEMPLATE_DIR.parent / "static" / "game.js").read_text(encoding="utf-8")
    assert "e.key === 'Enter'" in game_js
    assert "e.key === ' '" in game_js
    assert "e.ctrlKey" in game_js
