"""منطق الألعاب الصافي (بدون ديسكورد) — سهل الاختبار ومشترك بين الأوامر."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def parse_number(text: str) -> Optional[int]:
    """يقبل الأرقام العربية والإنجليزية، ويرفض أي نص آخر."""
    t = text.strip().translate(ARABIC_DIGITS)
    if t.startswith("-") and t[1:].isdigit():
        return int(t)
    return int(t) if t.isdigit() and len(t) <= 9 else None


@dataclass(frozen=True)
class MathProblem:
    text: str
    answer: int
    level: str


def make_math_problem(rng: random.Random) -> MathProblem:
    op = rng.choice(["+", "-", "×", "÷"])
    if op == "×":
        a, b = rng.randint(3, 15), rng.randint(3, 15)
        return MathProblem(f"{a} × {b}", a * b, "متوسط")
    if op == "÷":
        b, q = rng.randint(2, 12), rng.randint(2, 15)
        return MathProblem(f"{b * q} ÷ {b}", q, "متوسط")
    if op == "+":
        a, b = rng.randint(15, 300), rng.randint(15, 300)
        return MathProblem(f"{a} + {b}", a + b, "سهل" if a + b < 200 else "متوسط")
    a = rng.randint(50, 400)
    b = rng.randint(10, a)
    return MathProblem(f"{a} − {b}", a - b, "سهل" if a < 150 else "متوسط")


def scramble(word: str, rng: random.Random) -> str:
    """يبعثر الحروف مع ضمان اختلاف الناتج عن الكلمة الأصلية متى أمكن."""
    letters = list(word)
    if len(set(letters)) < 2:
        return word
    for _ in range(20):
        rng.shuffle(letters)
        if "".join(letters) != word:
            break
    return "".join(letters)


def scramble_hint(word: str) -> str:
    """تلميح: أول حرف وعدد الحروف."""
    return f"تبدأ بـ «{word[0]}» • عدد الحروف {len(word)}"


class GuessState:
    """حالة لعبة خمّن الرقم: النطاق المعروف للجميع ومحاولات كل لاعب."""

    def __init__(self, secret: int, maximum: int, max_tries: int):
        self.secret = secret
        self.maximum = maximum
        self.low, self.high = 1, maximum
        self.max_tries = max_tries
        self.tries: dict[int, int] = {}
        self.total = 0

    def remaining(self, user_id: int) -> int:
        return self.max_tries - self.tries.get(user_id, 0)

    def guess(self, user_id: int, n: int) -> str:
        """يرجع: 'blocked' | 'out' | 'higher' | 'lower' | 'win'."""
        if self.remaining(user_id) <= 0:
            return "blocked"
        if not 1 <= n <= self.maximum:
            return "out"
        self.tries[user_id] = self.tries.get(user_id, 0) + 1
        self.total += 1
        if n == self.secret:
            return "win"
        if n < self.secret:
            self.low = max(self.low, n + 1)
            return "higher"
        self.high = min(self.high, n - 1)
        return "lower"


def quiz_options(answer: str, wrong: list[str], rng: random.Random) -> list[str]:
    seen, opts = set(), []
    for o in [answer] + list(wrong):
        if o not in seen:
            seen.add(o)
            opts.append(o)
    opts = opts[:4]
    rng.shuffle(opts)
    return opts
