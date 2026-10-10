# Scenario Runbooks

> تولیدشده از `app/support/runbooks.py` با `python -m scripts.export_runbooks`. منبع اصلی همان فایل است.

## پرداخت در ارائه‌دهنده موفق است ولی سفارش pending مانده (webhook نرسیده)
**slug:** `webhook-missing`

- **نشانه:** مشتری می‌گوید مبلغ کسر شده یا پیامک موفق گرفته؛ در پنل پذیرنده سفارش هنوز «در انتظار پرداخت» است.
- **شرط اجرا:** پرداخت در وضعیت pending است و در timeline هیچ رویداد webhook از نوع processed برای آن نیست.
- **داده لازم:** یکی از این‌ها کافی است: payment_id، order_id یا provider_reference. زمان تقریبی رخداد.
- **بررسی‌ها (قبل از هر تغییر):**
  1. پرداخت را با payment_id پیدا کنید و وضعیت payment و order را یادداشت کنید.
  2. در timeline بررسی کنید آیا webhook با وضعیت rejected/failed ثبت شده است (اگر بله، runbook امضا یا مبلغ را ببینید).
  3. query «webhook_events_for_payment» را اجرا کنید؛ نتیجه خالی یعنی webhook اصلاً به سامانه نرسیده است.
  4. قبل از هر تغییری، وضعیت را از طریق verify از ارائه‌دهنده بپرسید؛ به حرف مشتری یا اسکرین‌شات اتکا نکنید.
- **query / API:** `webhook_events_for_payment`, `open_payments_vs_provider`, `status_transitions_for_payment`
- **APIها:** `GET /api/v1/payments/{payment_id}`؛ `POST /api/v1/payments/{payment_id}/verify`؛ `GET /api/v1/timeline?payment_id={payment_id}`
- **اقدام مجاز:**
  - اجرای verify (فقط‌خواندنی در ارائه‌دهنده؛ وضعیت داخلی را بر اساس پاسخ رسمی به‌روز می‌کند).
  - درخواست ارسال مجدد webhook از ارائه‌دهنده (در lab: redeliver-webhook) در صورت نیاز به آزمودن مسیر webhook.
- **اقدام ممنوع:**
  - تغییر دستی status در دیتابیس.
  - paid کردن سفارش بدون پاسخ verify.
- **شرط escalation:** اگر verify وضعیت SUCCEEDED بدهد ولی مبلغ متفاوت باشد، یا اگر webhook برای چند پرداخت پشت سر هم نرسیده باشد (احتمال قطعی endpoint)، به تیم فنی/یکپارچه‌سازی ارجاع شود.
- **روش verification:** پس از verify: payment=succeeded و order=paid؛ در timeline رویداد payment.status_changed با منبع verify دیده شود و review_reason خالی باشد. یک verify دوم نباید تغییری ایجاد کند (no_status_change_needed).
- **پیام نمونه به پذیرنده:** پرداخت سفارش {order_id} از سمت ارائه‌دهنده موفق تأیید شد و وضعیت سفارش در سامانه شما به «پرداخت‌شده» تغییر کرد. علت تأخیر، نرسیدن اعلان خودکار (webhook) بود. لطفاً سفارش را طبق روال پردازش کنید.

## دریافت چندباره یک webhook
**slug:** `duplicate-webhook`

- **نشانه:** در لاگ‌ها یک event_id چند بار دیده می‌شود؛ پذیرنده نگران ثبت دوباره پرداخت یا ارسال دوباره کالاست.
- **شرط اجرا:** provider_events برای یک event_id بیش از یک ردیف دارد.
- **داده لازم:** payment_id یا event_id.
- **بررسی‌ها (قبل از هر تغییر):**
  1. query «duplicate_webhook_events» را اجرا کنید: باید دقیقاً یک processed و بقیه duplicate باشند.
  2. در status_transitions_for_payment بررسی کنید که گذار pending → succeeded فقط یک بار رخ داده است.
- **query / API:** `duplicate_webhook_events`, `webhook_events_for_payment`, `status_transitions_for_payment`
- **APIها:** `GET /api/v1/timeline?payment_id={payment_id}`؛ `GET /api/v1/logs?event_type=webhook.duplicate`
- **اقدام مجاز:**
  - توضیح رفتار مورد انتظار به پذیرنده؛ تحویل شواهد (تعداد processed=1).
- **اقدام ممنوع:**
  - حذف ردیف‌های duplicate از provider_events؛ این ردیف‌ها شاهد هستند.
- **شرط escalation:** اگر بیش از یک processed برای یک event_id دیده شد، یا یک گذار وضعیت دو بار ثبت شد: P1، فوراً به تیم فنی (احتمال race condition).
- **روش verification:** processed=1 برای event_id، یک گذار وضعیت، و یک سفارش paid. ارسال مجدد همان رویداد باید پاسخ 200 با result=duplicate بدهد.
- **پیام نمونه به پذیرنده:** رویداد پرداخت سفارش {order_id} چند بار از ارائه‌دهنده رسیده، اما فقط یک بار در سامانه اعمال شده است. پرداخت یک بار ثبت شده و نیازی به اقدام یا برگشت وجه نیست.

## Timeout در ایجاد تراکنش و وضعیت نامشخص (unknown)
**slug:** `timeout-unknown`

- **نشانه:** مشتری خطای «زمان پاسخ تمام شد» دیده، ولی ممکن است پول کسر شده باشد. پرداخت در سامانه unknown است.
- **شرط اجرا:** payment.status=unknown یا review_reason=provider_timeout.
- **داده لازم:** payment_id یا order_id. زمان خطا.
- **بررسی‌ها (قبل از هر تغییر):**
  1. مطمئن شوید پرداخت unknown است، نه failed. timeout به‌تنهایی دلیل شکست نیست.
  2. در timeline ببینید provider_reference دریافت شده یا نه (در timeout معمولاً خالی است).
  3. verify را اجرا کنید؛ verify با merchant_reference (همان payment_id) استعلام می‌کند و به provider_reference نیاز ندارد.
  4. اگر خود verify هم timeout شد (HTTP 504)، وضعیت را دست نزنید و چند دقیقه بعد تکرار کنید.
- **query / API:** `payment_overview`, `open_payments_vs_provider`, `errors_by_correlation`
- **APIها:** `POST /api/v1/payments/{payment_id}/verify`
- **اقدام مجاز:**
  - اجرای verify و ثبت نتیجه در پرونده.
  - اگر ارائه‌دهنده رکوردی ندارد، طبق قرارداد شبیه‌ساز پرداخت failed می‌شود و مشتری می‌تواند دوباره پرداخت کند.
- **اقدام ممنوع:**
  - failed کردن دستی پرداخت فقط به دلیل timeout.
  - ایجاد تلاش پرداخت جدید قبل از روشن شدن وضعیت تلاش قبلی (سیستم با ACTIVE_PAYMENT_EXISTS جلوگیری می‌کند).
- **شرط escalation:** اگر پس از چند verify (مثلاً سه بار در ۳۰ دقیقه) پاسخ قطعی نیامد، یا تعداد timeoutها ناگهان زیاد شد، به تیم فنی و ارائه‌دهنده ارجاع شود.
- **روش verification:** پس از verify وضعیت قطعی (succeeded/failed) است، provider_reference ثبت شده و review_reason خالی است. در گزارش تطبیق بعدی status_mismatch برای این پرداخت نیست.
- **پیام نمونه به پذیرنده:** وضعیت پرداخت سفارش {order_id} بعد از خطای زمان پاسخ، از ارائه‌دهنده استعلام شد. نتیجه: {result}. اگر نتیجه ناموفق است، مبلغی از مشتری برداشت نشده است و او می‌تواند دوباره پرداخت کند.

## مغایرت مبلغ به دلیل واحد ریال/تومان در پیکربندی adapter
**slug:** `amount-unit-mismatch`

- **نشانه:** مبلغ اعلام‌شده ارائه‌دهنده یک‌دهم (یا ده برابر) مبلغ سفارش است؛ پرداخت موفق ولی سفارش paid نشده است.
- **شرط اجرا:** review_reason=amount_mismatch یا مغایرت amount_mismatch در تطبیق.
- **داده لازم:** payment_id و نام پذیرنده.
- **بررسی‌ها (قبل از هر تغییر):**
  1. query «amount_unit_check» را اجرا کنید: sent_amount و sent_amount_unit را با amount_irr مقایسه کنید.
  2. provider_amount_unit پذیرنده را با قرارداد NavaPay Simulator (IRR) مقایسه کنید.
  3. بررسی کنید آیا فقط یک پذیرنده یا همه درگیرند؛ اگر یکی، احتمال پیکربندی است، نه باگ عمومی.
- **query / API:** `amount_unit_check`, `payment_overview`
- **APIها:** `POST /api/v1/payments/{payment_id}/verify`؛ `GET /api/v1/reconciliation/exceptions?exception_type=amount_mismatch`
- **اقدام مجاز:**
  - جمع‌آوری شواهد و توقف تأیید سفارش‌های آن پذیرنده تا اصلاح.
  - اعلام دقیق تنظیم اشتباه به پذیرنده/تیم یکپارچه‌سازی.
- **اقدام ممنوع:**
  - اصلاح دستی مبلغ در دیتابیس.
  - paid کردن سفارش با مبلغ کمتر.
  - تصمیم درباره دریافت مابه‌التفاوت از مشتری؛ این تصمیم مالی/تجاری است.
- **شرط escalation:** همیشه escalate: تغییر پیکربندی adapter و تصمیم مالی درباره پرداخت‌های کم‌مبلغ خارج از اختیار پشتیبانی است. شدت P1 اگر هنوز پرداخت جدید با تنظیم اشتباه در جریان است.
- **روش verification:** پس از اصلاح پیکربندی، یک پرداخت آزمایشی جدید باید sent_amount = amount_irr داشته باشد و بدون amount_mismatch به succeeded برسد.
- **پیام نمونه به پذیرنده:** در بررسی پرداخت سفارش {order_id} مشخص شد مبلغ ارسالی به ارائه‌دهنده یک‌دهم مبلغ سفارش بوده است (تنظیم واحد مبلغ روی «تومان» به‌جای «ریال»). تا اصلاح تنظیم، سفارش‌ها را بر اساس این پرداخت‌ها ارسال نکنید. موضوع برای اصلاح پیکربندی و تصمیم درباره پرداخت‌های انجام‌شده به تیم مربوط ارجاع شد.

## رد webhook به دلیل امضای نامعتبر (HTTP 401)
**slug:** `invalid-signature`

- **نشانه:** ارائه‌دهنده اعلام می‌کند webhook ارسال شده، ولی سامانه پذیرنده 401 برگردانده و سفارش pending مانده است.
- **شرط اجرا:** provider_events.status=rejected با reason=invalid_signature یا missing_signature.
- **داده لازم:** payment_id یا provider_reference و زمان ارسال. هرگز خود secret را درخواست نکنید.
- **بررسی‌ها (قبل از هر تغییر):**
  1. query «rejected_webhooks» را اجرا کنید: آیا همه webhookها رد می‌شوند یا فقط یکی؟
  2. اگر همه از یک زمان به بعد رد می‌شوند، احتمال تغییر/چرخش secret در یک طرف بالاست.
  3. از پذیرنده بپرسید آیا اخیراً secret در پنل ارائه‌دهنده یا تنظیمات سامانه تغییر کرده است (بدون درخواست مقدار آن).
- **query / API:** `rejected_webhooks`, `webhook_events_for_payment`
- **APIها:** `POST /api/v1/simulator/payments/{payment_id}/redeliver-webhook`؛ `POST /api/v1/payments/{payment_id}/verify`
- **اقدام مجاز:**
  - verify برای روشن‌کردن وضعیت پرداخت‌های معطل.
  - راهنمایی پذیرنده برای هم‌سان‌کردن secret از مسیر امن خودش.
  - درخواست ارسال مجدد webhook پس از اصلاح (محدود به سقف تلاش).
- **اقدام ممنوع:**
  - غیرفعال کردن بررسی امضا برای «رفع سریع».
  - دریافت secret در تیکت، چت یا ایمیل.
- **شرط escalation:** اگر secret دو طرف یکسان تأیید شده ولی امضا همچنان رد می‌شود (احتمال تغییر بدنه توسط proxy یا encoding)، به تیم فنی ارجاع شود.
- **روش verification:** webhook ارسال مجدد با 200 و result=processed پذیرفته شود؛ payment=succeeded، order=paid. لاگ‌ها نباید هیچ مقدار secret یا امضا را نشان دهند.
- **پیام نمونه به پذیرنده:** اعلان‌های پرداخت به دلیل ناهماهنگی کلید امضای webhook رد می‌شدند. پس از هماهنگ‌سازی کلید در تنظیمات شما، اعلان مجدد با موفقیت پذیرفته شد و سفارش {order_id} پرداخت‌شده است. لطفاً کلید را فقط از مسیر امن پنل منتقل کنید و در پیام یا تیکت ارسال نکنید.

## تکرار درخواست با Idempotency-Key (پاسخ تکراری یا HTTP 409)
**slug:** `idempotency-conflict`

- **نشانه:** برنامه پذیرنده پس از قطعی شبکه درخواست را دوباره فرستاده؛ یا خطای IDEMPOTENCY_KEY_REUSED گرفته است.
- **شرط اجرا:** پاسخ با هدر Idempotent-Replayed: true یا خطای 409 IDEMPOTENCY_KEY_REUSED.
- **داده لازم:** مقدار Idempotency-Key و زمان درخواست‌ها؛ correlation_id پاسخ.
- **بررسی‌ها (قبل از هر تغییر):**
  1. query «idempotency_key_usage» را با کلید اجرا کنید.
  2. اگر بدنه یکسان بوده: پاسخ ذخیره‌شده برگشته و پرداخت دوم ساخته نشده است (رفتار درست).
  3. اگر بدنه متفاوت بوده: برنامه پذیرنده یک کلید را برای دو عملیات متفاوت استفاده کرده است.
- **query / API:** `idempotency_key_usage`, `errors_by_correlation`
- **APIها:** `POST /api/v1/payments (with Idempotency-Key)`
- **اقدام مجاز:**
  - توضیح قاعده به پذیرنده: یک کلید = یک عملیات؛ برای عملیات جدید کلید جدید.
- **اقدام ممنوع:**
  - حذف رکورد idempotency برای «باز کردن» کلید.
- **شرط escalation:** اگر با کلید و بدنه یکسان دو پرداخت ساخته شده باشد: باگ، P1، تیم فنی.
- **روش verification:** تکرار درخواست با همان کلید و بدنه پاسخ یکسان (همان payment_id) بدهد و تعداد payments برای سفارش ۱ باشد.
- **پیام نمونه به پذیرنده:** درخواست دوم با همان Idempotency-Key و همان اطلاعات، پرداخت جدیدی نساخت و همان پاسخ قبلی را برگرداند؛ بنابراین پرداخت تکراری ایجاد نشده است. خطای 409 زمانی رخ می‌دهد که یک کلید با اطلاعات متفاوت دوباره استفاده شود؛ لطفاً برای هر عملیات جدید کلید جدید بسازید.

## پرداخت موفق روی سفارش لغوشده (ناسازگاری وضعیت)
**slug:** `order-payment-state-conflict`

- **نشانه:** مشتری پرداخت کرده ولی سفارش در پنل «لغوشده» است؛ یا گزارش می‌شود وضعیت پرداخت و سفارش با هم نمی‌خواند.
- **شرط اجرا:** review_reason=order_state_conflict یا query «order_payment_conflicts» نتیجه دارد.
- **داده لازم:** order_id یا payment_id.
- **بررسی‌ها (قبل از هر تغییر):**
  1. timeline را بخوانید: زمان لغو سفارش را با زمان موفقیت پرداخت مقایسه کنید.
  2. لاگ order.cancelled_with_active_payment را ببینید: سفارش وقتی لغو شده که تلاش پرداخت هنوز باز بوده است.
  3. verify اجرا کنید تا مطمئن شوید پرداخت واقعاً موفق است.
- **query / API:** `order_payment_conflicts`, `status_transitions_for_payment`
- **APIها:** `GET /api/v1/timeline?payment_id={payment_id}`؛ `POST /api/v1/payments/{payment_id}/verify`
- **اقدام مجاز:**
  - ارائه دو گزینه به پذیرنده: (۱) refund کامل، (۲) احیای سفارش در سامانه خودش.
  - پس از تصمیم مکتوب پذیرنده، ثبت refund آزمایشی.
- **اقدام ممنوع:**
  - تغییر سفارش cancelled به paid توسط پشتیبانی (state machine هم اجازه نمی‌دهد).
  - refund بدون تصمیم پذیرنده.
- **شرط escalation:** اگر الگو تکرار می‌شود (لغو خودکار سفارش قبل از پایان مهلت پرداخت)، به تیم محصول برای اصلاح زمان‌بندی لغو.
- **روش verification:** پس از refund: payment=refunded و مجموع refund برابر مبلغ؛ یا اگر سفارش احیا شد، پذیرنده تأیید مکتوب داده است.
- **پیام نمونه به پذیرنده:** مشتری سفارش {order_id} پس از لغو سفارش در سامانه شما، پرداخت را با موفقیت کامل کرده است. لطفاً اعلام کنید سفارش را احیا و ارسال می‌کنید یا مبلغ به مشتری برگشت داده شود.

## مغایرت‌های تطبیق گزارش تراکنش ارائه‌دهنده
**slug:** `reconciliation-exceptions`

- **نشانه:** پس از بارگذاری گزارش CSV ارائه‌دهنده، ردیف‌های بدون تطبیق، اختلاف مبلغ/وضعیت یا تکراری دیده می‌شود.
- **شرط اجرا:** اجرای تطبیق exception باز دارد.
- **داده لازم:** شناسه اجرای تطبیق (run_id) و نام فایل گزارش.
- **بررسی‌ها (قبل از هر تغییر):**
  1. مغایرت‌ها را بر اساس نوع گروه‌بندی کنید؛ هر نوع علت متفاوتی دارد.
  2. status_mismatch: verify اجرا کنید (اغلب webhook نرسیده یا timeout).
  3. amount_mismatch: runbook واحد مبلغ را دنبال کنید.
  4. provider_only و duplicate: از ارائه‌دهنده توضیح مکتوب بخواهید؛ ردیفی را حذف نکنید.
  5. internal_only: تاریخ گزارش و زمان پرداخت را مقایسه کنید (مرز روز در وقت تهران).
- **query / API:** `reconciliation_open_exceptions`
- **APIها:** `POST /api/v1/reconciliation/runs`؛ `GET /api/v1/reconciliation/exceptions`؛ `POST /api/v1/reconciliation/exceptions/{id}/case`؛ `POST /api/v1/reconciliation/exceptions/{id}/resolve`
- **اقدام مجاز:**
  - ایجاد پرونده برای هر مغایرت یا گروه مغایرت.
  - ثبت نتیجه بررسی در resolution_note.
- **اقدام ممنوع:**
  - حذف یا ویرایش ردیف گزارش یا مبلغ داخلی («اصلاح مالی»).
  - بستن مغایرت بدون شاهد.
- **شرط escalation:** مغایرت‌های مبلغ و provider_only با ارزش بالا به تیم مالی/تسویه؛ duplicate به ارائه‌دهنده.
- **روش verification:** پس از اقدام، اجرای مجدد تطبیق با گزارش اصلاح‌شده برای همان ردیف‌ها exception نمی‌سازد یا علت باقی‌مانده مستند است.
- **پیام نمونه به پذیرنده:** در تطبیق گزارش تراکنش‌های تاریخ {date}، {count} مورد مغایرت پیدا شد. موارد در حال بررسی است و هیچ رکوردی حذف یا اصلاح مالی نشده است. نتیجه هر مورد جداگانه اطلاع داده می‌شود.

## رد درخواست refund بیش از مبلغ قابل استرداد
**slug:** `refund-limit`

- **نشانه:** پذیرنده هنگام ثبت استرداد خطای 422 با پیام «مبلغ استرداد از مانده قابل استرداد بیشتر است» می‌گیرد.
- **شرط اجرا:** خطای VALIDATION_ERROR روی amount_irr در POST refunds.
- **داده لازم:** payment_id و مبلغ درخواستی.
- **بررسی‌ها (قبل از هر تغییر):**
  1. مبلغ پرداخت، refunded_irr و refundهای در جریان را در payment_overview ببینید.
  2. مطمئن شوید استرداد قبلی دوباره ثبت نشده باشد (Idempotency-Key).
- **query / API:** `payment_overview`
- **APIها:** `POST /api/v1/payments/{payment_id}/refunds`
- **اقدام مجاز:**
  - اعلام مانده قابل استرداد به پذیرنده.
- **اقدام ممنوع:**
  - دور زدن سقف استرداد با چند درخواست موازی یا ویرایش دیتابیس.
- **شرط escalation:** اگر مجموع refundها از مبلغ بیشتر ثبت شده باشد: باگ، P1.
- **روش verification:** مجموع refundهای succeeded ≤ مبلغ پرداخت؛ وضعیت partially_refunded/refunded با مبلغ‌ها سازگار است.
- **پیام نمونه به پذیرنده:** برای پرداخت سفارش {order_id} حداکثر {remaining} ریال قابل استرداد است. لطفاً مبلغ را اصلاح و دوباره ثبت کنید.

## خطاهای 400، 401، 404 و 422 در فراخوانی API
**slug:** `api-auth-validation`

- **نشانه:** برنامه پذیرنده خطای HTTP می‌گیرد و نمی‌داند علت چیست.
- **شرط اجرا:** پاسخ خطا با ساختار error.code و correlation_id.
- **داده لازم:** correlation_id از بدنه یا هدر X-Correlation-ID پاسخ، endpoint و زمان. بدنه کامل درخواست لازم نیست.
- **بررسی‌ها (قبل از هر تغییر):**
  1. query «errors_by_correlation» یا صفحه لاگ‌ها را با correlation_id فیلتر کنید.
  2. 401: هدر X-API-Key ارسال نشده یا مقدار آن با محیط یکی نیست.
  3. 422: فهرست details نشان می‌دهد کدام فیلد مشکل دارد (مثلاً مبلغ اعشاری یا رشته).
  4. 404: شناسه اشتباه یا مربوط به محیط دیگر است.
  5. 400: JSON نامعتبر یا هدر لازم (مثل Idempotency-Key) ارسال نشده.
- **query / API:** `errors_by_correlation`
- **APIها:** `GET /api/v1/logs?correlation_id={correlation_id}`
- **اقدام مجاز:**
  - ارسال نمونه درخواست درست (curl/Postman) بدون کلید واقعی.
- **اقدام ممنوع:**
  - درخواست ارسال API key در تیکت.
- **شرط escalation:** اگر درخواست طبق مستندات درست است ولی خطا تکرار می‌شود: تیم فنی با correlation_id.
- **روش verification:** تکرار درخواست اصلاح‌شده پاسخ 2xx بدهد و لاگ همان correlation_id را با status_code موفق نشان دهد.
- **پیام نمونه به پذیرنده:** خطای دریافتی ({status} {code}) به دلیل {reason} بود. نمونه درخواست صحیح پیوست است. برای پیگیری‌های بعدی لطفاً correlation_id پاسخ را ارسال کنید.

## نمونه‌های Bug / Incident

این سه نمونه از اجرای واقعی سناریوهای همین lab ساخته شده‌اند (نتیجه‌ها از خروجی `scripts/seed_demo_data.py` و تست‌ها). هیچ باگ تاریخی ادعا نمی‌شود؛ هدف نشان‌دادن شکل گزارش است.

### INC-01: پرداخت موفق نزد ارائه‌دهنده، سفارش هنوز pending
- **Summary:** پرداخت نزد NavaPay موفق شده، ولی سفارش در `pending_payment` است چون webhook دریافت نشده.
- **Environment:** lab محلی، Python 3.12، SQLite، `python -m scripts.reset_demo_data`.
- **Reproduction steps:** ۱) سفارش و پرداخت بسازید. ۲) `POST /api/v1/simulator/payments/{payment_id}/complete` با `{"outcome":"succeeded","webhook":"skip"}`. ۳) `GET /api/v1/payments/{payment_id}`. ۴) `POST .../verify`.
- **Expected result:** قبل از verify، وضعیت تا روشن شدن باقی بماند؛ بعد از verify، پرداخت `succeeded` و سفارش `paid`.
- **Actual result (lab run):** قبل از verify: `pending` / `pending_payment`؛ بعد از verify: `succeeded` / `paid`.
- **Evidence:** خروجی سناریوی `webhook-missing`؛ خط زمانی پرداخت.
- **Root cause:** در lab، ارسال webhook عمداً حذف شده است (`webhook: skip`). علت در سامانه واقعی باید از سمت ارائه‌دهنده یا endpoint بررسی شود؛ lab فقط مسیر بازیابی (verify) را نشان می‌دهد.
- **Resolution:** استعلام verify از ارائه‌دهنده؛ تغییر وضعیت فقط بر اساس پاسخ رسمی.
- **Regression test:** `tests/test_payment_flows.py::test_happy_path_end_to_end` (مسیر عادی) و سناریوی `webhook-missing` در تست‌های دستی.

### INC-02: تلاش پرداخت دوم وقتی پرداخت اول unknown است
- **Summary:** بعد از timeout، ساخت پرداخت دوم برای همان سفارش نباید مجاز باشد.
- **Environment:** lab محلی، هدر `X-Lab-Simulate: provider_timeout`.
- **Reproduction steps:** ۱) `POST /api/v1/payments` با `X-Lab-Simulate: provider_timeout` (پاسخ 202). ۲) `POST /api/v1/payments` دوم برای همان سفارش.
- **Expected result:** HTTP 409 با کد `ACTIVE_PAYMENT_EXISTS` و راهنمای verify.
- **Actual result (lab run):** HTTP 409 با `ACTIVE_PAYMENT_EXISTS`؛ پرداخت اول `unknown` باقی ماند.
- **Evidence:** خروجی سناریوی `timeout-unknown`.
- **Root cause:** not applicable (رفتار مورد انتظار طراحی است).
- **Resolution:** not applicable.
- **Regression test:** سناریوی `timeout-unknown` و بخش ۸ از پرامپت (timeout و unknown تا verify).

### INC-03: مبلغ ارسالی به ارائه‌دهنده یک‌دهم مبلغ سفارش
- **Summary:** پذیرنده‌ای با واحد تومان تنظیم شده است؛ مبلغ ارسالی ۲۵,۰۰۰ بوده و ارائه‌دهنده آن را ریال خوانده.
- **Environment:** پذیرنده `mer_daneshyar_irt` در داده نمونه.
- **Reproduction steps:** ۱) سفارش ۲۵۰,۰۰۰ ریال برای این پذیرنده. ۲) پرداخت بسازید و `sent_amount` را بخوانید. ۳) پرداخت موفق را با webhook عادی ارسال کنید.
- **Expected result:** مبلغ ارسالی با واحد صریح باشد؛ مغایرت پرچم شود؛ پرداخت به `succeeded` نرود.
- **Actual result (lab run):** `sent_amount` = 25,000 با واحد IRT؛ webhook با `failed`؛ پرداخت `pending` با `review_reason=amount_mismatch`.
- **Evidence:** خروجی سناریوی `amount-unit-mismatch`؛ پرس‌وجوی `amount_unit_check`.
- **Root cause (تست پیکربندی):** واحد adapter روی IRT تنظیم شده است. این یک آزمایش پیکربندی است و ادعا نمی‌شود درگاه‌های واقعی چنین رفتاری دارند.
- **Resolution:** پیکربندی باید اصلاح شود و تصمیم درباره پرداخت‌های انجام‌شده به تیم مربوط ارجاع شود؛ پشتیبان مبلغ را دستی اصلاح نمی‌کند.
- **Regression test:** `tests/test_payment_flows.py::test_unit_misconfiguration_is_flagged_not_marked_paid`.
