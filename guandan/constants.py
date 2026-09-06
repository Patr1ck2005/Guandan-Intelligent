"""Game constants for Guandan (掼蛋)."""

NUM_PLAYERS = 4
CARDS_PER_PLAYER = 27
DECK_SIZE = 54
TOTAL_CARDS = 108
MIN_STRAIGHT_LEN = 5
STRAIGHT_LEN = 5  # 官方规则: 顺子恰好 5 张 (江苏竞赛规则), no variable-length straights
MIN_CONSECUTIVE_PAIRS = 3
MIN_CONSECUTIVE_TRIPLES = 2
MIN_BOMB_SIZE = 4
MAX_BOMB_SIZE = 8
STRAIGHT_FLUSH_SIZE = 5
ROCKET_SIZE = 2
MAX_WILDS_IN_DECK = 2
LOWEST_LEVEL = 2
HIGHEST_LEVEL = 14  # Ace

# Players opposite each other are partners
TEAMS = {0: 0, 1: 1, 2: 0, 3: 1}

RANK_DISPLAY = {
    2: '2', 3: '3', 4: '4', 5: '5', 6: '6', 7: '7', 8: '8',
    9: '9', 10: '10', 11: 'J', 12: 'Q', 13: 'K', 14: 'A',
    15: 'SJ', 16: 'BJ',
}

SUIT_DISPLAY = {0: 'C', 1: 'D', 2: 'H', 3: 'S', 4: ''}

# Ranks that cannot appear in straights
STRAIGHT_FORBIDDEN_RANKS = frozenset({2, 15, 16})
STRAIGHT_MAX_START = 10  # 10,J,Q,K,A
