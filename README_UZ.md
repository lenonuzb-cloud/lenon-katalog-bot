# LENON katalog boti (@lenonkatalogbot)

Lenon 55 guruhini boshqaradigan, kecha-kunduz ishlaydigan katalog boti.

## Bot ichidagi 8 ta "agent"

| № | Vazifasi | Fayl |
|---|---|---|
| 1 | Rasmni tahrirlaydi: burchagini to'g'rilaydi, yorug'lik/tiniqlik, kvadrat (1:1) qiladi | `images.py` |
| 2 | Rasmning yuqori o'rtasiga LENON logosini qo'yadi | `images.py` |
| 3 | Guruhning kerakli bo'limiga joylaydi (rasm slayd-shousi + jadval + agent tugmalari) va katalogga qo'shadi | `publisher.py` |
| 4 | Nazoratchi: jadvaldagi xatolarni (narx, kod, takror kodlar) va botdagi nosozliklarni topib, sizga xabar beradi | `checker.py` |
| 5 | Siz bilan ishlaydi: menyu, "qolmadi / keldi" belgilash, tezkor buyruqlar | `admin.py` |
| 6 | Jadvallarni tahrirlashga yordam beradi (narx, nom, kod, guruh, o'chirish, yangi mahsulot) | `admin.py`, `index.html` |
| 7 | O'zgarishlar haqida Muhokama bo'limida a'zolarni xabardor qiladi (bir necha o'zgarishni bitta xabarga yig'adi) | `notify.py` |
| 8 | Har kuni 21:00 da va so'raganingizda hisobot beradi | `report.py` |

Katalog — Telegram ichida ochiladigan mini ilova (`index.html`): LENON → dizaynlar → mahsulotlar,
BODEN, Naborlar; qidiruv; NEW / QOLMADI / KELDI belgilari. Adminlar shu yerning o'zida tahrir qila oladi.

## Ishga tushirish (bir marta)

### 1. Kerakli ma'lumotlar
- **BOT_TOKEN** — @BotFather bergan token (hech kimga bermang).
- **OWNER_ID** — sizning Telegram ID raqamingiz. @userinfobot ga /start yozsangiz, "Id" raqamini beradi.

### 2. GitHub'ga yuklash
1. github.com da hisob oching (bo'lsa — kiring).
2. **New repository** → nomi `lenon-katalog-bot` → **Private** → Create.
3. **uploading an existing file** havolasini bosing va shu papkadagi BARCHA fayl va papkalarni
   sudrab tashlang → **Commit changes**.

### 3. Render'ga joylash (bepul)
1. render.com → GitHub bilan kiring.
2. **New → Blueprint** → `lenon-katalog-bot` repozitoriyasini tanlang.
3. So'ralganda **BOT_TOKEN** va **OWNER_ID** ni kiriting → **Apply**.
4. 3–5 daqiqada tayyor bo'ladi. Manzil shunday bo'ladi: `https://lenon-katalog-bot-XXXX.onrender.com`

### 4. Uxlab qolmasligi uchun (bepul)
Bepul serverlar 15 daqiqa jim tursa uxlaydi. Bot o'zini o'zi uyg'otib turadi, lekin ishonch uchun:
cron-job.org → bepul hisob → **Create cronjob** → URL: `https://...onrender.com/health` → har **10 daqiqada**.

### 5. BotFather'da katalog tugmasi
@BotFather → `/mybots` → @lenonkatalogbot → **Bot Settings → Configure Mini App → Enable Mini App** →
URL: `https://...onrender.com/app`

### 6. Guruhga ulash
1. Botga /start yozing — "Siz bot egasisiz" degan javob keladi.
2. Botni **Lenon 55** guruhiga qo'shing va **admin** qiling (xabar yuborish, tahrirlash, o'chirish, mahkamlash, bo'limlarni boshqarish).
3. Har bir bo'limning ichida bir marta yozing:
   - LENON → `/bolim lenon`
   - BODEN → `/bolim boden`
   - Rasmlar → `/bolim rasmlar`
   - LENON nabor 44 → `/bolim nabor44`
   - LENON nabor 33 → `/bolim nabor33`
   - LENON nabor 39 → `/bolim nabor39`
   - Muhokama → `/bolim muhokama`

   Bot buyruqni o'chiradi va sizga "bo'lim ulandi" deb yozadi.

### 7. Eski postlarni ko'chirish
Guruhdagi barcha jadvallar (14 ta LENON, 7 ta BODEN, naborlar) botga allaqachon kiritilgan.
Faqat rasmlari yo'q — ularni berish uchun guruhdagi har bir eski postni botga **forward** qiling,
bot rasmlarni o'zi tegishli dizaynga biriktiradi. So'ng: **⚙️ Sozlamalar → 📤 Katalogni guruhga joylash**.
Bot yangi postlarni joylagach, eski postlarni o'chirib tashlang (bot faqat o'zi yuborgan postlarni tahrirlay oladi).

## Kundalik ishlatish

- **Rasm yuborish** → bot tahrirlaydi, logo qo'yadi, "qayerga?" deb so'raydi → yangi dizayn / nabor / mavjud dizayn.
- **📦 Qolmadi / Keldi** → bo'lim → dizayn → mahsulotlarni bosib belgilang → Saqlash.
- Tezkor: `14-NL YA5724 qolmadi`, `YA5724 keldi`, `Y936-2 qolmadi`.
- **➕ Yangi mahsulot** → `Aval | Aval kv 15 | YA5999 | 17$` (NEW belgisi 7 kun turadi).
- **✏️ Tahrirlash** → katalog ilovasida mahsulotni bosib o'zgartirasiz.
- **📊 Hisobot**, **🛠 Tekshirish** (nazoratchi).
- **⚙️ Sozlamalar** → admin qo'shish (u avval botga /start yozgan bo'lishi kerak), agent username'lari.

## Ma'lumotlar qayerda saqlanadi?
Har bir o'zgarishdan keyin bot katalogning zaxira nusxasini (`lenon_baza.json`) sizning bot bilan chatingizga
yuboradi va mahkamlab qo'yadi. Server qayta ishga tushsa, bot shu fayldan hammasini tiklaydi —
**shu mahkamlangan faylni o'chirmang.**
