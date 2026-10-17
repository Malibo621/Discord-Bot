# خريطة المشروع (للمطوّر)

## البنية

```
main.py                 نقطة التشغيل: تحميل الإضافات (cogs)، مزامنة الأوامر، قفل النسخة الواحدة
cogs/                   كل إضافة = ميزة كاملة (أوامرها + واجهتها). هنا تُضاف الأوامر
  panel.py              لوحة /panel (الأقسام، الإعدادات، المتجر، الصلاحيات)
  cmd_admin.py          لوحة /commands (تعطيل الأوامر، الأوصاف، الاختصارات)
  settings.py           /settings و /permissions بالأوامر
  economy.py            الرصيد، التحويل، المتصدرون، سجل العمليات، /points
  shop.py               المتجر وشراء الرتب والمنتجات وإدارتها
  games.py              الألعاب والفعاليات (مع bot/game_logic و bot/answers)
  music.py              الموسيقى: المشغّل، اللوحة (Components V2)، الطابور
  moderation.py         التحذيرات والكتم والطرد والحظر والمسح
  protection.py         السبام والروابط والتخريب والدخول الجماعي
  logging_events.py     سجلات الأحداث
  info.py               /help /userinfo /serverinfo /avatar /ping
  voice_home.py         الاتصال الصوتي الدائم 24/7
bot/                    منطق مشترك لا يعرف أوامر ديسكورد المحددة
  settings_schema.py    ★ تعريف كل الإعدادات + شرح كل إعداد (SETTING_HELP) + فئات الصلاحيات
  db.py                 قاعدة البيانات (SQLite) وكل استعلاماتها وترقياتها
  checks.py             فحص الصلاحيات (allowed / require / admin_only)
  registry.py           سجل الأوامر وأوصافها وتخصيصاتها
  shortcuts.py          الاختصارات النصية (!ban)
  ui.py                 مكوّنات واجهة مشتركة (Embeds، Paginator، BaseView…)
  canvas.py             ★ نظام التصميم للصور: ألوان، مسافات، مكوّنات رسم
  cards.py              قوالب البطاقات (المحفظة، الملف، المتصدرون، المتجر، الموسيقى، التحذيرات)
  gamecards.py          قوالب صور الألعاب
  wheel.py              عجلة الروليت
  fonts.py              نظام الخطوط (سلسلة احتياط، شكل عربي)
  brand.py / images.py  شعار السيرفر وجلب الصور بأمان
  answers.py            مطابقة الإجابات العربية الذكية
  game_logic.py / game_data.py   منطق الألعاب والمحتوى الافتراضي
tests/                  اختبارات بلا اتصال بديسكورد (محاكاة): test_bot / test_answers / test_features
docs/                   هذا المجلد
assets/fonts/           خطوطك الخاصة (اختياري، خارج git)
data/                   قاعدة البيانات والسجلات (لا تُرفع)
```

## كيف أضيف…

### إعدادًا جديدًا (يظهر في اللوحة تلقائيًا)
1. أضف `Setting(...)` في `bot/settings_schema.py` ضمن مجموعة (`group`) موجودة. المجموعة الجديدة تحصل على قسم خاص تلقائيًا.
2. أضف شرحه في `SETTING_HELP` في الملف نفسه (اختبار تلقائي يفشل إن نسيت).
3. اقرأه في الكود بـ `settings = await db.get_settings(guild_id); settings["key"]`.

### أمر سلاش جديد
1. أضفه في الإضافة المناسبة داخل `cogs/` بـ `@app_commands.command(...)`.
2. صلاحية بفئة: `@require("moderation")`، وللمسؤولين فقط `@admin_only()` (من `bot/checks.py`).
3. وصف الخيارات: `bot/registry.py` (جدول الأوصاف)، وإن كان جذرًا لا يصلح كاختصار نصي أضفه إلى `NO_SHORTCUT_ROOTS`.
4. أعد تشغيل البوت ليُزامَن. وأضف اختبارًا في `tests/test_features.py`.

### قسمًا جديدًا في `/panel`
أضف `Section(...)` في `_SECTIONS` داخل `cogs/panel.py` واذكر مجموعة إعداداته، ثم ضعه في `CATEGORIES` ليظهر تحت العنوان المناسب في الرئيسية.

### بطاقة صورة جديدة
ابنِها بمكوّنات `bot/canvas.py` (`Canvas.header` · `card` · `hero` · `chip` · `tile` · `progress`) في `cards.py` أو `gamecards.py`، وأرسلها بـ `images.branded(guild, fn, ...)` ليظهر شعار السيرفر. عدّل الألوان والمسافات في `canvas.py` فقط ليتغير كل شيء معًا.

### إضافة جديدة (cog)
أنشئ ملفًا في `cogs/` فيه `async def setup(bot)`، وأضف اسمه إلى `COGS` في `main.py`.

## الاختبارات
```bash
python tests/test_bot.py
python tests/test_answers.py
python tests/test_features.py
```
