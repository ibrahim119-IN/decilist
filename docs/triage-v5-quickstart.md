# مساعد إصلاح الاختبارات — V5

شغّله على مشروع Python موثوق فيه عنده اختبار بيفشل واختبارات رجوع موجودة. مفيش Playground ولا فورم: المساعد ياخد نفس الاختبارات، ينسخ المشروع، يجمع الدوال اللي اتنفذت، ويجرّب تعديل صغير. النسخة دي تجربة هندسية؛ نتيجة الاختبارات هي معيار قبول التعديل.

## على الجهاز ده

المشروع والموديل والمجلدات المؤقتة موجودين على L. شغّل من PowerShell:

```powershell
$env:TEMP='L:\CodexData\Decilist\tmp'
$env:TMP=$env:TEMP
$env:PYTHONPATH='L:\CodexData\Decilist\outputs\decilist\packages\python\src'
& 'L:\CodexData\Decilist\work\.venv\Scripts\python.exe' -m decilist.triage_v5 `
  --project 'L:\Projects\my-app' `
  --test 'tests/test_checkout.py' `
  --regression 'tests/test_cart.py' `
  --work-dir 'L:\CodexData\Decilist\work\my-app-triage-01' `
  --model-config 'L:\CodexData\Decilist\work\triage-v3-model\decilist-model.json' `
  --capture-failure-text
```

بدّل مسار المشروع وأسماء اختباراته الموجودة. اختار مجلد عمل جديد لكل تشغيل؛ الأمر بيعمل المجلدات الناقصة بأمان وبيسيب النتائج السابقة. الموديل المحلي المطلوب شغال على `127.0.0.1:8792`. الملف المحدد بيحمل هوية الموديل، مش مفتاح خدمة سحابية. `--capture-failure-text` بيسمح بإرسال تفاصيل فشل الاختبار للموديل المحلي؛ احذفه لو القيم حساسة.

النتيجة بتعرض الفرضية، التعديل، والاختبارات اللي اتشغلت. `hypothesis_supported_by_tests` معناها إن اختبارات الفشل والرجوع عدّت في النسخة المنفصلة؛ ده مش ضمان إن كل سلوك المشروع صحيح. `needs_review` أو `unverified` معناها مفيش إصلاح مثبت. السبب وملفات الأدلة بيتحفظوا حتى لو المحاولة فشلت.

المشروع الأصلي مبيتغيرش افتراضيًا. لو عايز الأمر يطبّق تعديل نجح في الاختبارات، زوّد `--apply` من بداية التشغيل. هيراجع إن ملفاتك ماتغيرتش أثناء التجربة قبل النقل. راجع الـdiff وشغّل اختبارات مشروعك الأوسع قبل الدمج. المساعد مش مسموح له يعدّل الاختبارات أو إعداداتها عشان يخليها تعدّي.

## من كود موجود

ثبّت حزمة Python واعتماديات الاختبارات مرة واحدة في بيئة التطوير من نسخة المستودع، وثبّت اعتماديات مشروعك المعتادة في نفس البيئة. مثلًا من جذر نسخة Decilist:

```bash
python -m pip install -c requirements-dev.lock -e "./packages/python[dev]"
```

الموديل وخادمه المحلي لهما إعداد منفصل؛ الأمر ده لا ينزّل أوزانًا. بعد تشغيل الخادم المحدد في ملف الهوية، استدعي المسار من أداة التطوير أو بعد فشل اختباراتك:

```python
from decilist.triage_v5 import run_triage_v5, format_text

report = await run_triage_v5(
    project=project_path,
    tests=["tests/test_checkout.py"],
    regressions=["tests/test_cart.py"],
    work_dir=new_work_directory,
    model_config=model_config_path,
    capture_failure_text=True,
)
print(format_text(report))
```

اختار اختبارات الرجوع اللي تحمي السلوك المرتبط بالعطل. مفيش تعريف قرار منفصل لكل bug. لو مشروعك فيه ملفات Python مباشرة في الجذر، اختار `--production-root .` صراحةً؛ غير كده الجذور الافتراضية هي `src` و`packages/python/src` و`vendor`. تشغيل الاختبارات بيتم بصلاحياتك العادية؛ استخدم مشاريع موثوق فيها.

## إيه اللي بنقيسه؟

بنحسب الوقت من بداية الشغل لحد إصلاح يعدّي نفس الاختبارات المحمية، شامل جمع السياق وانتظار الموديل والمحاولات الفاشلة والإصلاح اليدوي لو لزم. [بروتوكول المقارنة](triage-v5-experiment.md) اتكتب قبل تجارب الموديل. [النتيجة الفعلية](triage-v5-results.md): إصلاحان من 6، ووسيط التجربة بالمساعد أبطأ من الأدوات العادية. نتائج الوكلاء الآلية منفصلة عن قياس وقت مطور بشري؛ تجربة بشرية لم تُنفذ بعد.
