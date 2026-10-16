"""اختبارات مطابقة الإجابات الذكية (bot/answers.py) — تعمل بدون اتصال: python tests/test_answers.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bot import answers as A  # noqa: E402
from bot.game_data import QUESTIONS, WORDS  # noqa: E402


def smart(user, truth, wrong=()):
    return A.match(user, A.parse_alternatives(truth), wrong, mode="smart")


# (ما يكتبه المستخدم، الإجابة المعتمدة، متوقع القبول، الوصف)
CASES = [
    # --- همزات
    ("انقرة", "أنقرة", True, "حذف الهمزة"),
    ("إنقرة", "أنقرة", True, "همزة قطع بدل وصل"),
    ("أنقره", "أنقرة", True, "همزة + تاء مربوطة"),
    ("ايفرست", "إيفرست", True, "ألف بدل إ"),
    ("اوتاوا", "أوتاوا", True, "أ ← ا"),
    ("آسيا", "اسيا", True, "آ ← ا"),
    ("مسؤول", "مسئول", True, "ؤ/ئ"),
    # --- تشكيل وتطويل
    ("المُشْتَرِي", "المشتري", True, "تشكيل كامل"),
    ("الصَّحْرَاء", "الصحراء", True, "شدة وسكون"),
    ("المشـــتري", "المشتري", True, "تطويل"),
    # --- التاء المربوطة والهاء، الياء والألف المقصورة
    ("جامعه", "جامعة", True, "ة ← ه"),
    ("جامعة", "جامعه", True, "ه ← ة"),
    ("المشترى", "المشتري", True, "ى ← ي"),
    # --- مسافات وترقيم
    ("  ليوناردو    دافنشي  ", "ليوناردو دافنشي", True, "مسافات زائدة"),
    ("ليوناردودافنشي", "ليوناردو دافنشي", True, "بدون مسافة"),
    ("ليوناردو، دافنشي!", "ليوناردو دافنشي", True, "ترقيم"),
    ("الصحراء الكبرى.", "الصحراء الكبرى", True, "نقطة في الآخر"),
    ("ابنسينا", "ابن سينا", True, "دمج كلمتين"),
    # --- أرقام عربية
    ("١٠٠", "100", True, "أرقام هندية"),
    ("۱۰۰", "100", True, "أرقام فارسية"),
    # --- بدون «ال» التعريف
    ("مشتري", "المشتري", True, "حذف ال"),
    ("المشتري", "مشتري", True, "إضافة ال"),
    # --- كلمات تمهيدية في بداية الإجابة فقط
    ("هي المشتري", "المشتري", True, "كلمة تمهيدية"),
    ("الجواب المشتري", "المشتري", True, "الجواب + إجابة"),
    ("المشتري هي", "المشتري", False, "كلمة زائدة في الآخر لا تُحذف"),
    ("زحل المشتري", "المشتري", False, "كلمة إضافية تغيّر المعنى"),
    # --- أخطاء إملائية بسيطة مقبولة (إجابات متوسطة/طويلة، حرف أول صحيح)
    ("ليوناردو دافنسي", "ليوناردو دافنشي", True, "ش ← س (متشابهان)"),
    ("ثاني اكسيد الكاربون", "ثاني أكسيد الكربون", True, "زيادة ألف"),
    ("كانبيرا", "كانبرا", True, "زيادة ياء"),
    ("برازيلا", "برازيليا", True, "نقص ياء"),
    ("المحيط الهادء", "المحيط الهادئ", True, "ئ ← ء"),
    ("المشتيري", "المشتري", True, "حرف زائد"),
    ("سلوفاكيا", "سلوفاكيا", True, "مطابقة تامة"),
    ("استنابول", "استانبول", True, "تبديل متجاورين"),
    # --- تغيير حرف أساسي يغيّر المعنى = مرفوض
    ("غرب", "عرب", False, "ع ← غ (قصيرة)"),
    ("عطارف", "عطارد", False, "د ← ف حرف أساسي"),
    ("مدرين", "مدريد", False, "حرف مختلف"),
    ("المريخ", "المشتري", False, "كوكب آخر"),
    ("الزهره", "عطارد", False, "إجابة خاطئة صريحة"),
    ("كندا", "أوتاوا", False, "إجابة مختلفة"),
    ("تركيا", "أنقرة", False, "إجابة مختلفة"),
    ("برازيليا", "اسلام آباد", False, "غير ذات صلة"),
    # --- الحرف الأول مختلف
    ("ارازيليا", "برازيليا", False, "الحرف الأول مختلف"),
    ("نانبرا", "كانبرا", False, "الحرف الأول مختلف"),
    # --- إجابات قصيرة (تطابق تام فقط)
    ("فحد", "فهد", False, "قصيرة: تغيير حرف"),
    ("أحد", "أسد", False, "قصيرة: تشابه بالصدفة"),
    ("نبل", "النيل", False, "قصيرة"),
    ("جمل", "الجمل", True, "قصيرة مطابقة"),
    ("جمك", "الجمل", False, "قصيرة خطأ"),
    ("7", "7|سبعة", True, "رقم"),
    ("سبعه", "7|سبعة", True, "بديل نصي للرقم"),
    ("٧", "7|سبعة", True, "رقم هندي"),
    ("6", "7|سبعة", False, "رقم مختلف"),
    ("17", "7|سبعة", False, "رقم يحتوي الرقم"),
    ("70", "7|سبعة", False, "رقم مضاعف"),
    ("101", "100", False, "رقم قريب لا يقبل"),
    ("1000", "100", False, "رقم قريب لا يقبل"),
    # --- عبارات متعددة الكلمات: كلمات قصيرة لا تتساهل
    ("ابن سيما", "ابن سينا", False, "كلمة قصيرة داخل عبارة"),
    ("فان جوخ", "فان غوخ", False, "ج/غ مختلفان"),
    # --- بدائل معتمدة
    ("إديسون", "توماس إديسون|إديسون", True, "بديل معتمد"),
    ("اديسون", "توماس إديسون|إديسون", True, "بديل + همزة"),
    ("توماس اديسن", "توماس إديسون|إديسون", True, "خطأ بسيط في الاسم الكامل"),
    ("نيوتن", "توماس إديسون|إديسون", False, "اسم مختلف"),
    ("مئة", "100 درجة مئوية|100|مئة درجة|مئة", True, "بديل نصي"),
    ("100", "100 درجة مئوية|100|مئة درجة|مئة", True, "رقم فقط"),
    ("100 درجه مئويه", "100 درجة مئوية|100", True, "تاء مربوطة"),
    ("90", "100 درجة مئوية|100", False, "رقم آخر"),
    # --- فارغ وضجيج
    ("", "المشتري", False, "فارغ"),
    ("   ", "المشتري", False, "مسافات فقط"),
    ("!!!", "المشتري", False, "رموز فقط"),
    ("َُِ", "المشتري", False, "تشكيل فقط"),
]


def test_cases():
    bad = []
    for user, truth, expect, why in CASES:
        got = smart(user, truth).ok
        if got != expect:
            bad.append(f"{why}: user={user!r} truth={truth!r} expected={expect} got={got}")
    assert not bad, "\n".join(bad)
    print(f"OK answers: {len(CASES)} cases (hamza, diacritics, ta/ha, spaces, typos, key-letter changes, short, numbers, alternatives)")


def test_wrong_option_guard():
    # إن كانت إجابة المستخدم أقرب لإجابة خاطئة معروفة (أو مساوية لها) فلا تُقبل
    truth = ["abcdefgh"]
    assert A.match("abcdefg", truth, (), mode="smart").ok                     # حذف حرف: مقبول بلا حارس
    assert not A.match("abcdefg", truth, ["abcdefgx"], mode="smart").ok       # قريب من خاطئة بنفس المسافة: مرفوض
    assert not A.match("abcdefgx", truth, ["abcdefgx"], mode="smart").ok      # مطابق لخاطئة: مرفوض
    assert A.match("abcdefgh", truth, ["abcdefgx"], mode="smart").ok          # الصحيحة تُقبل دائمًا
    # على أسئلة اللعبة الحقيقية: الخيارات الخاطئة لا تُقبل أبدًا وإجاباتها الصحيحة وبدائلها تُقبل دائمًا
    for q, raw, wrong in QUESTIONS:
        alts = A.parse_alternatives(raw)
        for a in alts:
            assert A.match(a, alts, wrong, mode="smart").ok, (q, a)
            assert A.match(a.replace("ة", "ه").replace("أ", "ا"), alts, wrong, mode="smart").ok or any(
                ch.isdigit() for ch in a), (q, a)
        for w in wrong:
            assert not A.match(w, alts, wrong, mode="smart").ok, (q, w, alts)
    print(f"OK answers: wrong-option guard + all {len(QUESTIONS)} real questions (correct/alternatives accepted, wrong options rejected)")


def test_exact_mode_for_typing_games():
    for w in WORDS:
        assert A.same(w, w) and A.same(w, " " + w.replace("ة", "ه") + " ")
    assert A.same("مُكتبة", "مكتبه") and A.same("أسد", "اسد") and A.same("عمّان", "عمان")
    assert not A.same("المشتري", "مشتري")            # الكتابة تحتاج الكلمة كما ظهرت (بالـ ال)
    assert not A.same("مكتبه", "مكتبات") and not A.same("حاسوب", "حاسوبي") and not A.same("", "")
    # لا تساهل في وضع exact: خطأ حرف واحد يرفض
    for w in WORDS:
        if len(w) >= 5:
            typo = w[:2] + ("ظ" if w[2] != "ظ" else "ض") + w[3:]
            assert not A.match(typo, [w], mode="exact").ok, (w, typo)
    print("OK answers: exact mode (typing/scramble) tolerant only to normalization, never to typos")


def test_parse_and_safety():
    assert A.parse_alternatives("أ|ب| |ج") == ["أ", "ب", "ج"] and A.parse_alternatives("") == [""]
    assert len(A.parse_alternatives("|".join("x%d" % i for i in range(30)))) == A.MAX_ALTERNATIVES
    assert A.distance("abc", "abc") == 0 and A.distance("abc", "abd") == 2 and A.distance("ضرب", "ظرب") == 1
    assert A.distance("ab", "ba") == 1 and A.distance("abc", "ab") == 1
    # إدخال ضخم أو معادٍ: سريع ولا ينهار
    t = time.perf_counter()
    for text in ("ا" * 100000, "x" * 5000, "\u0000" * 50, "🔥" * 300, "​" * 1000):
        assert not A.match(text, ["المشتري"], ["زحل"], mode="smart").ok
    assert time.perf_counter() - t < 1.0
    # لا تعتمد النتيجة على الحالة أو على إدخال المستخدم لأي إعداد
    assert A.match("JUPITER", ["Jupiter"], mode="smart").ok and A.match("jupiter", ["Jupiter"], mode="exact").ok
    print("OK answers: alternatives parsing, weighted distance, huge/hostile input handled quickly")


test_cases()
test_wrong_option_guard()
test_exact_mode_for_typing_games()
test_parse_and_safety()
print("ALL ANSWER TESTS PASSED")
