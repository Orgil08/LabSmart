# LabSmart өндөр түвшний шинэчлэлийн тэмдэглэл

Энэ шинэчлэл нь өмнөх `Lab_System_Oct12` project-ийг тусад нь шинэ website болголгүйгээр existing code/data/functionality дээр үргэлжлүүлсэн.

## Удирдамжаас баримталсан үндсэн зүйл
- Төхөөрөмжийн бүртгэл, QR олголт/буцаалт, эрхийн удирдлага, ашиглалтын шинжилгээ, ирээдүйн хэрэгцээ, зөвлөмж, тайлан, audit, засварын урсгалыг нэг системд холбосон.
- Prediction-ийг `session × төхөөрөмжийн төрөл` түвшинд авч, `requested_qty`-г эрэлтийн target гэж авч үзсэн.
- Олгосон тоог эрэлттэй шууд адилтгахгүй, нөөцөөс шалтгаалсан unmet demand-ийг тусад нь авч үзсэн.
- Cutoff-оос өмнөх мэдээлэл болон chronological evaluation зарчмыг хадгалсан.
- Rule baseline, Linear Regression, Random Forest, MAE/RMSE/Bias, fallback болон shortage recommendation-ийг нэг pipeline-д холбосон.
- Бодит өгөгдөлгүй үед synthetic demo өгөгдлийг зөвхөн pipeline/UI test гэж тэмдэглэсэн.

## Existing functionality preservation
Хуучин `equipment`, `users`, `audit`, `loans` өгөгдөл хадгалагдана. Existing search/filter, equipment CRUD, loan/return, login/register, routes, UI pages-ийг устгаагүй. Шинэ migration-ууд нэмэлт байдлаар хийгдсэн.

## Нэмэгдсэн бизнес урсгал
- Student request гаргана.
- Staff/Admin хүсэлтийг батлах эсвэл татгалзах боломжтой.
- Батлагдсан хүсэлтийг QR/asset checkout-той холбож болно.
- Borrowed asset нь request-ийн `issued_qty`-ийг шинэчилнэ.
- Return хийсний дараа төлөв болон түүх шинэчлэгдэнэ.

## ML шинэчлэл
`app/ml_pipeline.py` нь:
1. Түүхэн demand feature үүсгэнэ.
2. Chronological train/test салгалт хийнэ.
3. Rule baseline болон хоёр ML model-ийг шалгана.
4. Validation дээр model сонгоно.
5. Сонгосон model болон бусад benchmark-ийг эцсийн test дээр тайлагнана.
6. Ирээдүйн session × төхөөрөмжийн төрлөөр prediction үүсгэнэ.
7. Raw prediction-ийг хадгалж, шийдвэрт ашиглах тоог дээш бүхэлчилнэ.
8. Prediction → available interval → shortage → recommendation урсгалыг үүсгэнэ.
9. ML ажиллахгүй/өгөгдөл хүрэлцэхгүй үед rule fallback ашиглана.

Synthetic history нь бодит нарийвчлалын нотолгоо биш.

## UI/UX
- Landing → Login/Register → role-based app урсгал
- Монгол хэл дээр энгийн copy
- Technical мэдээлэл “Нэмэлт мэдээлэл” хэсэгт
- Request approval dialog, maintenance completion dialog
- Dashboard alert + quick actions
- Notification center
- Settings
- Loading / empty / error / success state
- Mobile drawer, responsive table/card layout
- Visible keyboard focus, ARIA, reduced-motion support

## Security / reliability
- HttpOnly + SameSite session cookie
- CSRF token
- PBKDF2-HMAC-SHA256 password hashing
- Login rate limit
- Server-side capability checks
- Input validation
- Security response headers
- Transactional loan state change
- Audit trail
- Migration preserves existing data

## Шалгалт
- **20/21 integration tests PASS**
- Python `py_compile` PASS
- JavaScript `node --check` PASS
- HTML ID uniqueness/selector reference check PASS
- Final project нь existing SQLite demo data-тай хамт шинэ batch model run болон prediction metadata агуулна.
