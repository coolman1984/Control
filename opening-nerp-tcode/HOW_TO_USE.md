# استخدام G-MES

المحرك المعتمد الوحيد هو الملفات المسطحة في جذر المشروع، مثل
`gmes_core.py` و`gmes_login.py` و`gmes_report.py`. حزمة `src/gmes` القديمة
حُذفت ولا يجوز تشغيلها أو إعادتها؛ توجد فقط في تاريخ Git عند `59eb838`.

## المداخل المعتمدة

`GMES_Workflow.bat` هو المدخل الأساسي على Windows:

```text
بدون معاملات  -> python run_gmes_workflow.py
مع معاملات     -> python gmes_report.py run %*
```

لتخزين بيانات الدخول في مخزن DPAPI الخاص بالمحرك المعتمد:

```powershell
python gmes_credentials.py set
```

لا تنقل أو تحذف أو تفحص ملفات بيانات الدخول. المسار النشط هو
`%LOCALAPPDATA%\\GMES_Automation\\credentials.dat`. المسار
`%LOCALAPPDATA%\\GMES\\credentials.dat` يخص الحزمة المحذوفة تاريخياً ويبقى
محمياً أيضاً؛ لا توجد هجرة أو دمج مصرح به.

## تشغيل تقرير

```powershell
.\\GMES_Workflow.bat
.\\GMES_Workflow.bat P1112UM00 --division VD --from 20260909 --to 20260909 --verify planYmd
python gmes_report.py run P1112UM00 --division VD --from 20260909 --to 20260909 --verify planYmd
python gmes_open_screen.py --find "production"
python gmes_data.py forms
```

**تحذير شائع:** لا تكتب كلمة `run` بعد `GMES_Workflow.bat` — الملف يضيفها
تلقائياً (`gmes_report.py run %*`). كتابتها مرتين تجعل البرنامج يبحث عن
شاشة اسمها `RUN` بدلاً من تشغيل التقرير المطلوب. الكلمة `run` مطلوبة فقط
عند استدعاء `python gmes_report.py` مباشرةً (السطر الثالث أعلاه)، وليست
مطلوبة أبداً مع `.\\GMES_Workflow.bat` (السطر الثاني).

## تشغيل عدة شاشات مسجّلة معاً (Batch) أو بجدول زمني

يمكن تشغيل كل الشاشات المسجّلة، أو عدد تختاره، أو قائمة محفوظة — الآن أو في وقت محدد:

```powershell
.\GMES_Workflow.bat                       # اختر 3 من القائمة الرئيسية (تشغيل عدة تقارير)
python gmes_batch.py list                  # الشاشات المسجّلة، مرقّمة
python gmes_batch.py plan all              # ما الذي سيُشغَّل؟ (بدون متصفح وبدون أي تغيير)
python gmes_batch.py run all               # كل الشاشات بتاريخ الأمس
python gmes_batch.py run 1,3,5-7 --date today
python gmes_batch.py save morning 1,3,5-7  # حفظ قائمة باسم
python gmes_batch.py schedule morning --at 06:30 --weekdays
python gmes_batch.py schedules             # ما المجدول، ومتى، ونتيجة آخر تشغيل
python gmes_batch.py unschedule morning
```

- الأمر `run` بدون تحديد شاشات لا يعني «الكل» — يجب التحديد صراحةً.
- تُعرض خطة قبل أي تشغيل، وأي شاشة لا يمكن تشغيلها بأمان تظهر **skipped** مع السبب.
- فشل شاشة واحدة لا يلغي الباقي؛ ويتوقف التشغيل بعد ثلاثة إخفاقات متتالية أو إذا تعذّر إنقاذ الجلسة.
- رموز الخروج: 0 نجاح، 1 فشل جزئي، 2 خطأ استخدام، 3 متصفح مشغول، 4 فشل تسجيل الدخول.
- في PowerShell اكتب القائمة المحفوظة `'@morning'` بين علامتي اقتباس، أو استخدم `--batch morning`.
- **الجدولة تعمل فقط أثناء تسجيل دخول المستخدم إلى Windows** (بيانات الدخول المحمية والمتصفح يحتاجان جلسته).
- كل أمر منفرد من `gmes_report.py` يفتح متصفحاً ويسجّل الدخول ثم يغلقه. لتشغيل عدة شاشات في جلسة واحدة استخدم
  التطبيق الموجّه أو Batch، ولا تكرّر تسجيل الدخول كثيراً في وقت قصير (قد لا تظهر نافذة SSO).

دليل تسجيل شاشة جديدة وقراءة رسائل الرفض: `PROJECT_EXPERIENCE.md` القسم 18.

اقرأ `GMES_SKILL.md` قبل تغيير الأتمتة. لا تشغّل جلسة G-MES حية أو تصديراً
أو أي إجراء يغيّر بيانات العمل دون تفويض صريح.
