"""مطابقة الإجابات النصية للألعاب: تطبيع موحّد + تساهل إملائي محسوب (وليس عشوائيًا).

المبادئ:
1) التطبيع (fold) يُطبَّق على إجابة المستخدم والإجابة الصحيحة معًا قبل أي مقارنة: التشكيل والتطويل،
   أشكال الألف (أ إ آ ٱ ← ا)، الياء والألف المقصورة، الهمزة على واو/ياء، التاء المربوطة والهاء، الأرقام العربية
   والفارسية، علامات الترقيم، المسافات، والحروف المقارِنة للغة (ك/ی/ھ).
2) وضع exact: تساوي بعد التطبيع فقط (ألعاب الكتابة والتفكيك — لا مجال لأخطاء إملائية فيها).
3) وضع smart (أسئلة المعرفة): يقبل التطبيع أولًا، ثم خطأً إملائيًا بسيطًا فقط بشروط صارمة:
   - إجابات قصيرة (أقل من 5 حروف) أو فيها أرقام ← مطابقة تامة فقط.
   - تكلفة تحرير موزونة: حذف/إضافة/تبديل متجاورين = 1، استبدال حرفين متشابهين نطقًا/رسمًا (ض/ظ، س/ص، ت/ط،
     ذ/ز، ث/س) = 1، وأي استبدال آخر = 2. الحد: 1 لإجابة 5–8 حروف، 2 لإجابة 9 حروف فأكثر.
     فاستبدال حرف أساسي (ع↔غ، ب↔ن...) لا يُقبل أبدًا في الإجابات المتوسطة لأنه يكلّف 2.
   - الحرف الأول يجب أن يطابق، والكلمات القصيرة (3 حروف فأقل) داخل عبارة متعددة الكلمات تُطابق تمامًا.
   - حارس الأخطاء: إن كانت إجابة المستخدم أقرب (أو مساوية) لإحدى الإجابات الخاطئة المعروفة للسؤال ترفض.
   - إجابات بديلة معتمدة: تُكتب مفصولة بـ «|» (مثل: توماس إديسون|إديسون).
   - تُحذف كلمات تمهيدية شائعة من بداية إجابة المستخدم فقط (هو، هي، الجواب...) ولا تُحذف كلمات من المنتصف.
لا يعتمد شيء هنا على بيانات يرسلها المستخدم سوى نص الإجابة نفسه.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿­]")
_MARKS = re.compile("[ً-ٰٟۖ-ۭـ]")          # تشكيل + علامات قرآنية + تطويل
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LETTERS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ی": "ي", "ې": "ي",
    "ؤ": "و",
    "ة": "ه", "ہ": "ه", "ھ": "ه",
    "ک": "ك", "گ": "ك",
    "ء": "",
})
_PUNCT = re.compile(r"[^\w\s]|_", re.UNICODE)
# بعد التطبيع وحذف «ال» (فـ «الجواب» تصير «جواب»)
FILLERS = {"هو", "هي", "انه", "انها", "هذا", "هذه", "جواب", "الجواب", "اجابه", "الاجابه", "اجابتي", "اظن", "اعتقد"}
# أزواج متشابهة نطقًا/رسمًا يُسمح بالتبديل بينها بتكلفة 1 (وأي تبديل آخر 2)
CONFUSABLE = {frozenset(p) for p in ("ضظ", "سص", "تط", "ذز", "ثس")}
MAX_ALTERNATIVES = 8
MAX_INPUT = 160


def fold(text: str, *, strip_article: bool = False) -> str:
    """تطبيع نص للمقارنة (انظر وصف الوحدة). strip_article يحذف «ال» التعريف من أول كل كلمة طويلة."""
    t = unicodedata.normalize("NFKC", text or "")
    t = _INVISIBLE.sub("", t)
    t = _MARKS.sub("", t)
    t = t.translate(_DIGITS).translate(_LETTERS)
    t = _PUNCT.sub(" ", t).casefold()
    words = t.split()
    if strip_article:
        words = [w[2:] if w.startswith("ال") and len(w) > 3 else w for w in words]
    return " ".join(words)


_LOOSE_HAMZA = str.maketrans({"ؤ": "", "ئ": "", "ء": ""})


def compact(folded: str) -> str:
    return folded.replace(" ", "")


def loose(text: str, *, strip_article: bool = False) -> str:
    """تطبيع بديل يتجاهل همزة الواو/الياء (مسؤول ≡ مسئول)، ويُستخدم كمقارنة ثانية للتطابق التام."""
    return fold((text or "").translate(_LOOSE_HAMZA), strip_article=strip_article)


def parse_alternatives(raw: str) -> list[str]:
    """«توماس إديسون|إديسون» ← قائمة الإجابات المعتمدة (الأولى هي المعروضة)."""
    parts = [p.strip() for p in (raw or "").split("|")]
    out = [p for p in parts if p]
    return (out or [(raw or "").strip()])[:MAX_ALTERNATIVES]


def threshold(length: int) -> int:
    return 0 if length < 5 else 1 if length <= 8 else 2


def distance(a: str, b: str) -> int:
    """مسافة تحرير موزونة (Damerau-OSA): حذف/إضافة/تبديل متجاورين = 1، استبدال متشابه = 1، غيره = 2."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            if a[i - 1] == b[j - 1]:
                sub = 0
            else:
                sub = 1 if frozenset((a[i - 1], b[j - 1])) in CONFUSABLE else 2
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + sub)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


@dataclass(frozen=True)
class Match:
    ok: bool
    kind: str = "no"          # exact | fuzzy | no
    matched: str = ""         # الإجابة المعتمدة التي طابقت
    cost: int = 0


def _strip_fillers(words: list[str]) -> list[str]:
    while len(words) > 1 and words[0] in FILLERS:
        words = words[1:]
    return words


def _fuzzy_ok(user: str, truth: str) -> Optional[int]:
    """يرجع تكلفة التطابق التقريبي إن قُبلت، وإلا None."""
    cu, ct = compact(user), compact(truth)
    if any(ch.isdigit() for ch in ct) or len(ct) < 5:
        return None
    if cu[:1] != ct[:1]:
        return None
    limit = threshold(len(ct))
    if abs(len(cu) - len(ct)) > limit:
        return None
    uw, tw = user.split(), truth.split()
    if len(tw) > 1:
        if len(uw) != len(tw):
            return None                       # اختلاف عدد الكلمات ← تطابق تام فقط (يُعالَج قبل هذه الدالة)
        total = 0
        for a, b in zip(uw, tw):
            if len(b) <= 3 and a != b:
                return None                   # كلمة قصيرة داخل عبارة: لا تساهل
            total += distance(a, b) if a != b else 0
        cost = total
    else:
        cost = distance(cu, ct)
    return cost if cost <= limit else None


def match(user_text: str, answers: Sequence[str], wrong: Iterable[str] = (), *, mode: str = "smart") -> Match:
    """يقارن إجابة المستخدم بالإجابات المعتمدة. mode: exact | smart."""
    smart = mode == "smart"
    if len(user_text or "") > MAX_INPUT:      # رسائل طويلة جدًا ليست إجابات: لا نحسب لها مسافات تحرير
        return Match(False)
    uf = fold(user_text, strip_article=smart)
    if not compact(uf):
        return Match(False)
    candidates = [uf]
    if smart:
        stripped = " ".join(_strip_fillers(uf.split()))
        if stripped and stripped != uf:
            candidates.append(stripped)
    truths = [(a, fold(a, strip_article=smart)) for a in answers if compact(fold(a))]
    loose_user = compact(loose(user_text, strip_article=smart))
    for a, ta in truths:
        for u in candidates:
            if compact(u) == compact(ta):
                return Match(True, "exact", a, 0)
        if loose_user and loose_user == compact(loose(a, strip_article=smart)):
            return Match(True, "exact", a, 0)
    if not smart:
        return Match(False)
    best: Optional[Match] = None
    for a, ta in truths:
        for u in candidates:
            cost = _fuzzy_ok(u, ta)
            if cost is not None and (best is None or cost < best.cost):
                best = Match(True, "fuzzy", a, cost)
    if best is None:
        return Match(False)
    # حارس: لا نقبل إن كانت إجابة المستخدم أقرب لإجابة خاطئة معروفة من الصحيحة
    for w in wrong:
        fw = fold(w, strip_article=True)
        if not compact(fw):
            continue
        for u in candidates:
            if compact(u) == compact(fw) or distance(compact(u), compact(fw)) <= best.cost:
                return Match(False)
    return best


def same(a: str, b: str) -> bool:
    """تساوٍ بعد التطبيع فقط (ألعاب الكتابة): لا حذف لـ «ال» ولا تساهل."""
    ca = compact(fold(a))
    if not ca:
        return False
    return ca == compact(fold(b)) or compact(loose(a)) == compact(loose(b))
