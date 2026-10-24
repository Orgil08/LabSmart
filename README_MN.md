# LabSmart — Лабораторийн төхөөрөмжийн ухаалаг удирдлага
**С.Мөнх-Оргил · 23B1NUM0124 · Н.Мөнхцэцэг**

Энэ хувилбар нь өмнөх `Lab_System_Oct12` төслийн бүтэц, SQLite өгөгдөл, төхөөрөмжийн бүртгэл, хайлт/шүүлтүүр, нэвтрэлт болон бүртгэлийн логийг хадгалж, дээр нь LabSmart-ийн өндөр түвшний лабораторийн удирдлагын урсгалыг нэмсэн. Шинэчлэл нь тусдаа website биш, existing project дээрх өргөтгөл юм.

## Ажиллуулах
1. ZIP файлыг бүхэлд нь задална. Python 3.10+ хэрэгтэй.
2. Бүрэн ML batch ажиллуулахад `pandas` болон `scikit-learn` суусан байх шаардлагатай. Жишээ:
   `python -m pip install pandas scikit-learn`
3. Windows: `START_WINDOWS.bat` эсвэл `cd app && py -3 server.py --demo`.
4. macOS/Linux: `sh START_MAC_LINUX.sh`.
5. Хөтөчөөр `http://127.0.0.1:8000` нээнэ.
6. Зогсоохдоо terminal дээр `Ctrl+C`.

ML сангууд байхгүй үед систем үндсэн үйл ажиллагааг зогсоохгүй, дүрмийн `rule_fallback` ашиглана.

## Демо нэвтрэх эрх
- Админ: `admin` / `LabAdmin2026!`
- Оюутан: `viewer` / `LabViewer2026!`

Шинэ бүртгэлээр Оюутан эсвэл Багш/Лабораторийн ажилтан төрлийг сонгож болно. Админ эрхийг өөрөө сонгох боломжгүй.

## Хэрэгжсэн боломжууд
- Landing / welcome хуудас, нээлттэй төхөөрөмжийн каталог
- Монгол хэл дээрх modern dark SaaS интерфэйс
- Нэвтрэх, бүртгүүлэх, password show/hide, password strength, ойлгомжтой алдаа/ачаалалт
- Admin / staff / student эрхийн түвшинтэй role-based UI ба серверийн зөвшөөрөл
- Dashboard KPI, ашиглалт, төхөөрөмжийн төлөв, ирэх хэрэгцээ, анхааруулга, сүүлийн үйлдэл
- Төхөөрөмжийн бүртгэл, хайлт, шүүлтүүр, дэлгэрэнгүй, нэмэх/засах
- QR workflow: боломжтой браузерт camera scan, мөн төхөөрөмжийн дугаарыг гараар оруулах fallback
- Олголт / буцаалт, хугацаа хэтрэлт, нөхцөлийн бүртгэл
- **Хүсэлт → Батлах/татгалзах → QR-аар авах → Буцаах** workflow
- Хичээлийн session болон төхөөрөмжийн хүсэлт
- Ирээдүйн хэрэгцээний prediction, түүхий таамаг, боловсруулсан тоо, range
- **Rule baseline + Linear Regression + Random Forest** benchmark болон хугацааны дараалалтай test metrics
- Хомсдолын зөвлөмж, priority, “юу/яагаад/юу хийх” тайлбар, шийдвэрийн төлөв
- Засвар үйлчилгээ, засвар дуусгах урсгал
- Үйлдлийн түүх / audit
- Мэдэгдэл, unread count, mark as read / all read
- Ашиглалт & тайлан, хэрэглэгчийн ашиглалтын тойм, CSV таталт
- Админы системийн тохиргоо: стандарт олголтын хугацаа, low-stock босго, prediction horizon, notification toggle
- Keyboard focus, ARIA label, screen-reader-д ойлгомжтой нэршил, өнгөөр дангаар нь status ялгахгүй UI
- Responsive desktop / tablet / mobile layout, touch-friendly controls
- Security: HttpOnly session cookie, SameSite, CSRF token, PBKDF2 password hash, input validation, rate limit, security response headers

## ML / таамаглалын зарчим
Төслийн удирдамжийн дагуу таамаглах нэгж нь `session × төхөөрөмжийн төрөл`, target нь олголтоос өмнөх бодит `requested_qty` байна. Олгосон тоог эрэлттэй шууд адилтгахгүй. Cutoff-оос хойших ирц, олголт, буцаалтыг урьдчилан таамаглалын feature болгон ашиглахгүй.

Batch нь дараах аргуудыг benchmark хэлбэрээр хадгална:
- Дүрмийн baseline
- Өмнөх эрэлт/түүхийн логикт тулгуурласан feature
- Linear Regression
- Random Forest Regressor

Сонгосон ML model-ийг validation дээр сонгож, эцсийн chronological test дээр MAE/RMSE/Bias-ийг тусад нь тайлагнана. Хэрэв ML сан эсвэл түүх хүрэлцэхгүй бол `rule_fallback` ажиллана.

**Анхаарах:** `app/data/lab.db` дахь synthetic demo history нь зөвхөн pipeline, UI, алдааны урсгалыг шалгах зориулалттай. Үүнийг бодит ML нарийвчлалын нотолгоо гэж тайлбарлахгүй. Бодит байгууллагын хэмжилтэд бодит хүсэлтүүдийг шаардлагатай cutoff болон data dictionary-тайгаар цуглуулах хэрэгтэй.

## Existing data preservation
`app/data/lab.db` доторх өмнөх хэрэглэгч, төхөөрөмж, audit, loan мэдээллийг устгахгүй. Шинэ schema migration нь request approval, prediction metadata, model run, notification, settings болон maintenance талбарыг нэмэлт байдлаар оруулна. Хуучин `viewer` хэрэглэгчийг student эрхийн түвшинд тайлбарлана.

Түүхтэй төхөөрөмжийг физик устгахын оронд `retired` төлөвөөр хадгална. Нэг төхөөрөмжид зэрэг хоёр нээлттэй олголт үүсэхгүй бөгөөд олголт/төлөвийн өөрчлөлт transaction дотор хийгдэнэ.

## Туршилт
`cd app` дараа:

```text
python test_system.py
```

Одоогийн suite нь тусдаа түр SQLite сан дээр **21 integration test** ажиллуулж:
login, registration, role restriction, CRUD, search/filter, loan/return, request approval/QR checkout, ML batch/model comparison, recommendation, maintenance, reports, audit, notifications, settings, profile/onboarding, CSRF, logout болон restart persistence урсгалыг шалгана.

JavaScript syntax (`node --check`) болон Python syntax (`py_compile`) мөн шалгана.

## Файлын бүтэц
- `app/server.py` — HTTP API, authentication, role checks, business logic, migration
- `app/ml_pipeline.py` — cutoff-safe demand training/evaluation/prediction batch
- `app/schema.sql` — үндсэн хүснэгт, холбоос, индекс
- `app/static/index.html` — бүх UI view, modal, dialog
- `app/static/style.css` — responsive modern dark UI
- `app/static/app.js` — SPA routing, API calls, state, interaction
- `app/data/lab.db` — одоо байгаа SQLite өгөгдөл + migration/seed
- `app/test_system.py` — integration test suite
- `docs/` — тайлан, схем, интерфэйсийн материал, шинэчлэлийн тэмдэглэл

## Хязгаарлалт
Энэ багц нь гадны сервергүй локал сургалтын/демо орчин хэвээр. Байгууллагын production deployment хийхэд HTTPS termination, shared persistent session store, бодит email/password recovery, scheduled Python ML batch service, PostgreSQL migration, backup/restore болон observability/monitoring infrastructure-ийг нэмж холбоно.
