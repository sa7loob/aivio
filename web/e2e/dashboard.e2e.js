/* eslint-disable */
// E2E للوحة: Next (build) + FastAPI + worker + PostgreSQL حقيقيون، والزبائن عبر webhooks موقّعة.
// المتطلبات والتشغيل: docs/architecture/09-phase-6b-dashboard-ui.md (قسم «التجربة الشاملة»).
//
//   OWNER_URL=postgresql://app_owner:...@127.0.0.1:5432/agentdb node e2e/dashboard.e2e.js
//
// كل تشغيل يستخدم بريداً وأرقام قنوات وزبائن جديدة => لا يحتاج قاعدة بيانات فارغة.
const { execFileSync, execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

function loadPlaywright() {
  try {
    return require("playwright");
  } catch {
    return require(path.join(execSync("npm root -g").toString().trim(), "playwright"));
  }
}
const { chromium } = loadPlaywright();

const BASE = process.env.E2E_BASE_URL || "http://localhost:3000";
const MOCK = process.env.E2E_MOCK_URL || "http://127.0.0.1:8099";
const OWNER_URL = process.env.OWNER_URL;
const BACKEND = path.resolve(__dirname, "../../backend");
const PY = process.env.E2E_PYTHON || (fs.existsSync(`${BACKEND}/.venv/bin/python`) ? `${BACKEND}/.venv/bin/python` : "python3");
const SHOTS = process.env.E2E_SHOTS || path.join(__dirname, "screenshots");
if (!OWNER_URL) {
  console.error("OWNER_URL is required (app_owner, for ageing a conversation past 24h)");
  process.exit(2);
}
fs.mkdirSync(SHOTS, { recursive: true });

const RUN = Date.now().toString(36);
const digits = (n) => String(Math.floor(Math.random() * 10 ** n)).padStart(n, "0");
const PN1 = `1${digits(11)}`; // phone_number_id للقناة الأولى
const PN2 = `2${digits(11)}`;
const CUST = `21891${digits(7)}`; // زبون: +21891xxxxxxx => 091 xxx xxxx
const CUST_LOCAL = `0${CUST.slice(3, 5)} ${CUST.slice(5, 8)} ${CUST.slice(8)}`;
const CUST2 = `21892${digits(7)}`;
const OWNER_EMAIL = `owner-${RUN}@noor.ly`;
const PASS = "owner-pass-123";
const NOOR = "وكالة النور للحج والعمرة";

const results = [];
const problems = [];
const ok = (name) => {
  results.push(name);
  console.log(`PASS ${name}`);
};

// ------------------------------------------------------------------ backend helpers
function backend(args, loadEnv = false) {
  const cmd = loadEnv ? ["-c", 'set -a; . ./.env; set +a; exec "$@"', "_", PY, ...args] : null;
  const out = loadEnv
    ? execFileSync("bash", cmd, { cwd: BACKEND, stdio: ["ignore", "pipe", "pipe"] })
    : execFileSync(PY, args, { cwd: BACKEND, stdio: ["ignore", "pipe", "pipe"] });
  return out.toString().trim();
}
function customer(from, name, text, pnid = PN1) {
  backend(["-m", "scripts.dev_send_whatsapp", "--from", from, "--name", name, "--text", text, "--phone-number-id", pnid]);
}
function registerChannel(slug, pnid, display) {
  backend(["-m", "scripts.register_whatsapp_channel", "--tenant-slug", slug, "--tenant-name", NOOR,
    "--phone-number-id", pnid, "--display-name", display, "--test"], true);
}
function psql(sql) {
  return execFileSync("psql", [OWNER_URL, "-X", "-A", "-t", "-q", "-c", sql]).toString().trim();
}
async function mockStats() {
  return (await fetch(`${MOCK}/__mock/stats`)).json();
}
async function failSends(on) {
  await fetch(`${MOCK}/__mock/fail-sends`, { method: "POST", body: JSON.stringify({ on }) });
}

// ------------------------------------------------------------------ browser helpers
function watch(page, label) {
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`[${label}] console: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`[${label}] pageerror: ${e.message}`));
  page.on("response", (r) => {
    if (r.status() >= 500) problems.push(`[${label}] ${r.status()} ${r.url()}`);
  });
}
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });

// عناصر تخرج عن عرض الشاشة (مقصوصة بـ overflow أو تسبب تمريراً أفقياً)
async function assertFits(page, selectors, where) {
  const bad = await page.evaluate((sels) => {
    const out = [];
    for (const sel of sels) {
      for (const el of document.querySelectorAll(sel)) {
        const r = el.getBoundingClientRect();
        if (r.width && (r.left < -1 || r.right > window.innerWidth + 1)) {
          out.push(`${sel} [${Math.round(r.left)}, ${Math.round(r.right)}]`);
        }
      }
    }
    return out;
  }, selectors);
  if (bad.length) throw new Error(`off-screen on ${where}: ${bad.slice(0, 5).join(", ")}`);
}
const waitSelect = (page, label, pred) =>
  page.waitForFunction(
    ([l, p]) => {
      const s = document.querySelector(`select[aria-label="${l}"]`);
      return s && !s.disabled && (p === "empty" ? s.value === "" : s.value !== "");
    },
    [label, pred],
  );

(async () => {
  const browser = await chromium.launch();
  try {
    await failSends(false);
    // ============================================================ desktop: owner
    const owner = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: "ar-LY", acceptDownloads: true });
    const page = await owner.newPage();
    watch(page, "owner");

    await page.goto(`${BASE}/inbox`);
    await page.waitForURL(/\/login\?next=/);
    ok("unauthenticated /inbox redirects to /login?next=");

    // التسجيل => القنوات مع الترحيب
    await page.goto(`${BASE}/register`);
    await page.fill("#full_name", "صاحب الوكالة");
    await page.fill("#business_name", NOOR);
    await page.fill("#email", OWNER_EMAIL);
    await page.fill("#phone", "0912345678");
    await page.fill("#password", PASS);
    await page.getByRole("button", { name: "إنشاء الحساب" }).click();
    await page.waitForURL(/\/channels\?welcome=1/);
    await page.getByText("أهلاً بك في لوحة الوكيل الذكي").waitFor();
    await page.getByText("لا توجد قنوات مربوطة").waitFor();
    const connectHref = await page.getByRole("link", { name: "ربط قناة" }).first().getAttribute("href");
    if (!/^\/connect\?tenant=[0-9a-f-]{36}$/.test(connectHref)) throw new Error(`connect href ${connectHref}`);
    await shot(page, "01-channels-welcome");
    ok("register -> /channels?welcome=1 with welcome card, empty state, connect link carries tenant");

    await page.goto(`${BASE}${connectHref}`);
    await page.locator("#workspace").waitFor({ state: "visible" });
    if (!(await page.locator("#tenant-name").textContent()).includes("وكالة النور")) throw new Error("connect tenant");
    ok("/connect opens logged-in workspace for the current business");

    // القناة (سكربت الإدارة؛ Embedded Signup يحتاج Meta الحقيقي)
    const me = await page.evaluate(() => fetch("/api/v1/me").then((r) => r.json()));
    const slug = me.tenants[0].slug;
    registerChannel(slug, PN1, "+218 91 000 0001");
    await page.goto(`${BASE}/channels`);
    await page.locator(".channel-row").filter({ hasText: "مفعّلة" }).waitFor();
    await page.getByText("رقم تجريبي").waitFor();
    ok("channel appears as active on /channels");

    // رسالة زبون تظهر لحظياً بدون إعادة تحميل (SSE عبر Next)
    await page.getByRole("link", { name: "المحادثات" }).click();
    await page.waitForURL(/\/inbox$/);
    await page.getByText("لا توجد محادثات هنا").waitFor();
    await page.locator(".sidebar").getByText("متصل لحظياً").waitFor({ timeout: 15000 });
    ok("inbox empty state + live indicator connected");
    customer(CUST, "أبو محمد", "السلام عليكم، قداش عمرة رمضان؟");
    const item = page.locator(".conv-item").filter({ hasText: "أبو محمد" });
    await item.waitFor({ timeout: 15000 });
    ok("new conversation appeared live (no reload)");
    await item.filter({ hasText: "هلا بيك" }).waitFor({ timeout: 20000 });
    ok("bot reply preview updated live");

    await item.click();
    await page.waitForURL(/\/inbox\?c=/);
    await page.locator(".bubble.in").filter({ hasText: "قداش عمرة رمضان" }).waitFor();
    await page.locator(".bubble.out.bot").filter({ hasText: "هلا بيك" }).waitFor();
    await shot(page, "02-inbox-thread-bot");
    ok("thread shows customer (in) and bot (out) bubbles");

    await page.getByRole("button", { name: "استلام المحادثة" }).click();
    await page.getByText("البوت متوقف في هذه المحادثة").waitFor();
    await page.getByRole("button", { name: "إرجاع للبوت" }).waitFor();
    ok("takeover -> human banner + release button");

    const composer = page.getByLabel("نص الرد");
    await composer.fill("أهلاً أبو محمد، عمرة رمضان تبدأ من 4500 دينار. نبعثلك التفاصيل؟");
    await composer.press("Enter");
    const staffBubble = page.locator(".bubble.out:not(.bot)").filter({ hasText: "4500 دينار" });
    await staffBubble.locator('span[title="تم الإرسال"]').waitFor({ timeout: 15000 });
    if ((await composer.inputValue()) !== "") throw new Error("composer not cleared");
    ok("staff reply sent and delivery mark turns to sent (live)");

    const before = (await mockStats()).chat_calls;
    customer(CUST, "أبو محمد", "نبي احجز لـ 3 أشخاص");
    await page.locator(".bubble.in").filter({ hasText: "نبي احجز" }).waitFor({ timeout: 15000 });
    await page.waitForTimeout(6000); // أكثر من debounce الـ worker
    if ((await mockStats()).chat_calls !== before) throw new Error("bot answered during human takeover");
    ok("customer message during takeover appears live; bot stays silent");

    await failSends(true);
    await composer.fill("رسالة ستفشل أول مرة");
    await page.getByRole("button", { name: "إرسال" }).click();
    const failed = page.locator(".bubble.failed").filter({ hasText: "ستفشل" });
    await failed.waitFor({ timeout: 15000 });
    await failSends(false);
    await shot(page, "03-inbox-failed-send");
    await failed.getByRole("button", { name: "إعادة" }).click();
    await page.locator(".bubble.out").filter({ hasText: "ستفشل" }).locator('span[title="تم الإرسال"]').waitFor({ timeout: 15000 });
    ok("failed staff reply shows retry; retry delivers it");

    // رد الموظف علّم رسائل الزبون السابقة كمُجابة؛ رسالة جديدة أثناء الاستلام ثم الإرجاع
    customer(CUST, "أبو محمد", "نبي احجز عمرة رمضان");
    await page.locator(".bubble.in").filter({ hasText: "نبي احجز عمرة رمضان" }).waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "إرجاع للبوت" }).click();
    await page.getByRole("button", { name: "استلام المحادثة" }).waitFor();
    await page.locator(".bubble.out.bot").filter({ hasText: "سجلنا طلبك" }).waitFor({ timeout: 20000 });
    await page.getByText("لهذا الزبون طلب حجز مفتوح").waitFor({ timeout: 10000 });
    await page.locator(".conv-item.selected").getByText("طلب حجز").waitFor({ timeout: 10000 });
    await shot(page, "04-inbox-released-lead");
    ok("release -> bot answers pending message, lead badge appears live");

    await page.getByLabel("إسناد المحادثة").selectOption("");
    await waitSelect(page, "إسناد المحادثة", "empty");
    await page.getByLabel("إسناد المحادثة").selectOption({ label: "أنا (صاحب الوكالة)" });
    await waitSelect(page, "إسناد المحادثة", "set");
    ok("unassign then assign conversation to self");

    // ============================================================ leads
    await page.getByRole("link", { name: "طلبات الحجز" }).click();
    await page.waitForURL(/\/leads$/);
    const row = page.locator("table.table tbody tr").filter({ hasText: "سالم الورفلي" });
    await row.getByText("جديد").waitFor();
    ok("lead listed with status 'new'");
    await page.getByLabel("بحث").fill(CUST_LOCAL);
    await page.waitForTimeout(900);
    await row.waitFor();
    await page.getByLabel("الحالة").selectOption("booked");
    await page.getByText("لا توجد طلبات مطابقة").waitFor();
    await page.getByRole("button", { name: "مسح الفلاتر" }).first().click();
    await row.waitFor();
    ok("lead filters: local-format phone search, status filter empty state, clear filters");

    await row.click();
    const drawer = page.locator(".drawer");
    await drawer.getByText("مصراتة").waitFor();
    await drawer.getByText("أنشأ الطلب من المحادثة").waitFor();
    await drawer.locator("#lead-status").selectOption("contacted");
    await drawer.getByText("غيّر الحالة من «جديد» إلى «تم التواصل»").waitFor();
    await row.getByText("تم التواصل").waitFor();
    ok("drawer: status change -> timeline event + table row updated");
    await drawer.locator("#lead-assign").selectOption({ label: "صاحب الوكالة" });
    await drawer.getByText("أسند الطلب إلى صاحب الوكالة").waitFor();
    ok("drawer: assign staff -> timeline");
    await drawer.getByLabel("ملاحظة متابعة").fill("اتصلت بالزبون، يبي يأكد بعد الراتب");
    await drawer.getByRole("button", { name: "إضافة للسجل" }).click();
    await drawer.locator(".timeline").getByText("اتصلت بالزبون، يبي يأكد بعد الراتب").waitFor();
    ok("drawer: follow-up note added to timeline");
    await drawer.getByLabel("ملاحظات الطلب").fill("يفضّل الدفع على قسطين");
    await drawer.getByRole("button", { name: "حفظ الملاحظات" }).click();
    await drawer.getByText("عدّل الملاحظات").waitFor();
    ok("drawer: lead notes saved");
    await drawer.locator("#lead-status").selectOption("lost");
    await drawer.locator("#lost-reason").waitFor();
    if (!(await drawer.getByRole("button", { name: "حفظ" }).isDisabled())) throw new Error("lost save enabled without reason");
    await drawer.locator("#lost-reason").fill("السعر عالي");
    await drawer.getByRole("button", { name: "حفظ" }).click();
    await drawer.getByText("«تم التواصل» إلى «لم يتم»").waitFor();
    await drawer.locator("dd").filter({ hasText: "السعر عالي" }).waitFor();
    await shot(page, "05-leads-drawer");
    ok("drawer: 'lost' requires a reason, then saves it");
    await drawer.getByRole("link", { name: "فتح المحادثة" }).click();
    await page.waitForURL(/\/inbox\?c=/);
    await page.locator(".bubble.in").first().waitFor();
    ok("drawer link opens the conversation in the inbox");

    await page.getByRole("link", { name: "طلبات الحجز" }).click();
    await row.waitFor();
    const [dl] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "تصدير Excel (CSV)" }).click()]);
    const file = path.join(SHOTS, "export.csv");
    await dl.saveAs(file);
    const raw = fs.readFileSync(file);
    if (!(raw[0] === 0xef && raw[1] === 0xbb && raw[2] === 0xbf)) throw new Error("export: no BOM");
    const csv = raw.toString("utf8");
    if (!csv.includes("تاريخ الطلب") || !csv.includes("سالم الورفلي") || !csv.includes("لم يتم")) throw new Error("export content");
    ok(`export CSV downloaded (${dl.suggestedFilename()}) with BOM, Arabic headers, current data`);

    // ============================================================ team
    await page.getByRole("link", { name: "الفريق" }).click();
    await page.waitForURL(/\/team$/);
    await page.locator(".list-row").filter({ hasText: OWNER_EMAIL }).getByText("(أنت)").waitFor();
    await page.getByText("لا توجد دعوات").waitFor();
    await page.fill("#inv-note", "سارة - المبيعات");
    await page.getByRole("button", { name: "إنشاء رابط دعوة" }).click();
    const linkInput = page.getByLabel("رابط الدعوة");
    const inviteLink = await linkInput.inputValue();
    if (!inviteLink.startsWith(`${BASE}/register?invite=inv_`)) throw new Error(`invite link ${inviteLink}`);
    const wa = await page.getByRole("link", { name: "مشاركة عبر واتساب" }).getAttribute("href");
    if (!wa.startsWith("https://wa.me/?text=") || !decodeURIComponent(wa).includes(inviteLink)) throw new Error("wa link");
    await page.locator(".list-row").filter({ hasText: "سارة - المبيعات" }).getByText("بانتظار القبول").waitFor();
    await shot(page, "06-team-invite");
    await page.getByRole("button", { name: "تم" }).click();
    ok("team: invite link shown once with copy + WhatsApp share; invitation listed as pending");
    await page.fill("#inv-note", "دعوة للإلغاء");
    await page.getByRole("button", { name: "إنشاء رابط دعوة" }).click();
    await linkInput.waitFor();
    await page.getByRole("button", { name: "تم" }).click();
    page.once("dialog", (d) => d.accept());
    await page.locator(".list-row").filter({ hasText: "دعوة للإلغاء" }).getByRole("button", { name: "إلغاء" }).click();
    await page.locator(".list-row").filter({ hasText: "دعوة للإلغاء" }).getByText("ملغاة").waitFor();
    ok("team: revoke invitation");

    // الموظف المدعو في متصفح آخر
    const agentCtx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "ar-LY" });
    const ap = await agentCtx.newPage();
    watch(ap, "agent");
    await ap.goto(inviteLink);
    await ap.getByText("الانضمام للفريق").waitFor();
    if (await ap.locator("#business_name").count()) throw new Error("invite register shows business name");
    await ap.fill("#full_name", "سارة المبيعات");
    await ap.fill("#email", `sara-${RUN}@noor.ly`);
    await ap.fill("#password", "agent-pass-123");
    await ap.getByRole("button", { name: "إنشاء الحساب والانضمام" }).click();
    await ap.waitForURL(/\/inbox$/);
    await ap.locator(".conv-item").filter({ hasText: "أبو محمد" }).waitFor();
    ok("invited agent registers via link and lands on the inbox");
    await ap.getByRole("link", { name: "الفريق" }).click();
    await ap.getByText("هذه الصفحة للمدير فقط").waitFor();
    await ap.getByRole("link", { name: "طلبات الحجز" }).click();
    await ap.locator("table.table tbody tr").first().waitFor();
    if (await ap.getByRole("link", { name: "تصدير Excel (CSV)" }).count()) throw new Error("agent sees export");
    await ap.getByRole("link", { name: "القنوات" }).click();
    await ap.getByText("ربط القنوات وفصلها من صلاحية المدير").waitFor();
    if (await ap.getByRole("button", { name: "فصل" }).count()) throw new Error("agent sees disconnect");
    ok("agent: team admins-only notice, no export, no connect/disconnect");
    await page.goto(`${BASE}/team`);
    await page.locator(".list-row").filter({ hasText: "سارة المبيعات" }).waitFor();
    await page.locator(".list-row").filter({ hasText: "سارة - المبيعات" }).getByText("تم القبول").waitFor();
    ok("owner sees the new member and the invitation as accepted");

    // ============================================================ channels
    registerChannel(slug, PN2, "+218 92 000 0002");
    await page.goto(`${BASE}/channels`);
    const ch2 = page.locator(".channel-row").filter({ hasText: "+218 92 000 0002" });
    await ch2.getByText("مفعّلة").waitFor();
    page.once("dialog", (d) => d.accept());
    await ch2.getByRole("button", { name: "فصل" }).click();
    await ch2.getByText("مفصولة").waitFor();
    if (await ch2.getByRole("button", { name: "فصل" }).count()) throw new Error("disconnect still shown");
    await shot(page, "07-channels");
    ok("channels: disconnect with confirmation -> status 'disconnected'");

    // نافذة 24 ساعة ثم الإغلاق
    customer(CUST2, "علي", "السلام عليكم");
    await page.goto(`${BASE}/inbox`);
    const ali = page.locator(".conv-item").filter({ hasText: "علي" });
    await ali.waitFor({ timeout: 15000 });
    await page.waitForTimeout(6000); // رد البوت
    psql(`UPDATE conversations c SET last_inbound_at = now() - interval '25 hours' FROM contacts ct
           WHERE ct.id = c.contact_id AND ct.external_user_id = '${CUST2}'`);
    await ali.click();
    await page.getByText("مرت أكثر من 24 ساعة على آخر رسالة من الزبون").waitFor();
    if (!(await page.getByLabel("نص الرد").isDisabled())) throw new Error("composer enabled after 24h");
    ok("24h window closed -> warning and disabled composer");
    await page.getByRole("button", { name: "إغلاق" }).click();
    await page.getByText("المحادثة مغلقة. أي رسالة جديدة").waitFor();
    await page.getByRole("tab", { name: "المغلقة" }).click();
    await page.locator(".conv-item").filter({ hasText: "علي" }).waitFor();
    ok("close conversation -> closed banner, listed under 'closed'");

    // ============================================================ mobile (390px)
    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
      locale: "ar-LY", deviceScaleFactor: 2 });
    await mobile.addCookies(await owner.cookies());
    const mp = await mobile.newPage();
    watch(mp, "mobile");
    await mp.goto(`${BASE}/inbox`);
    const mItem = mp.locator(".conv-item").filter({ hasText: "أبو محمد" });
    await mItem.waitFor();
    if (await mp.locator(".sidebar").isVisible()) throw new Error("sidebar visible on mobile");
    await assertFits(mp, [".mobile-bar", ".conv-filters", ".conv-item", ".conv-item .preview"], "mobile inbox list");
    await shot(mp, "08-mobile-inbox-list");
    await mItem.tap();
    await mp.locator(".bubble").first().waitFor();
    if (await mp.locator(".conv-list").isVisible()) throw new Error("list visible with thread on mobile");
    const box = await mp.getByLabel("نص الرد").boundingBox();
    if (!box || box.y + box.height > 844) throw new Error("composer off-screen on mobile");
    await assertFits(mp, [".thread-head", ".thread-actions", ".composer", ".bubble"], "mobile thread");
    await shot(mp, "09-mobile-thread");
    await mp.getByRole("button", { name: "رجوع" }).tap();
    await mp.waitForURL(/\/inbox$/);
    await mItem.waitFor();
    ok("mobile: list -> thread (list hidden, composer on screen) -> back to list");
    await mp.getByRole("button", { name: "القائمة" }).tap();
    await mp.locator(".sidebar.open").waitFor();
    await shot(mp, "10-mobile-menu");
    await mp.locator(".sidebar.open").getByRole("link", { name: "طلبات الحجز" }).tap();
    await mp.waitForURL(/\/leads$/);
    await mp.locator("table.table tbody tr").first().waitFor();
    await assertFits(mp, [".page-head", ".filters", ".filters > *", "table.table", ".card"], "mobile leads");
    await shot(mp, "11-mobile-leads");
    await mp.locator("table.table tbody tr").first().tap();
    await mp.locator(".drawer").getByText("سجل المتابعة").waitFor();
    await assertFits(mp, [".drawer", ".drawer .select", ".drawer .textarea"], "mobile lead drawer");
    await shot(mp, "12-mobile-lead-drawer");
    await mp.keyboard.press("Escape");
    for (const p of ["team", "channels"]) {
      await mp.goto(`${BASE}/${p}`);
      await mp.locator(".page-head h1").waitFor();
      await mp.waitForTimeout(500);
      await assertFits(mp, [".page-head", ".card", ".list-row", ".btn"], `mobile ${p}`);
      await shot(mp, `13-mobile-${p}`);
    }
    ok("mobile: menu, leads table, drawer, team, channels fit 390px");

    // ============================================================ error state
    const errCtx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "ar-LY" });
    await errCtx.addCookies(await owner.cookies());
    const ep = await errCtx.newPage();
    await ep.route((u) => u.pathname === "/api/v1/leads", (r) => r.fulfill({ status: 502, body: "bad gateway" }));
    await ep.goto(`${BASE}/leads`);
    await ep.getByText("تعذّر التحميل").waitFor();
    await ep.getByText("خطأ في الخادم، حاول بعد قليل").waitFor();
    await shot(ep, "14-leads-error-state");
    ok("error state rendered in Arabic with retry when the API fails");

    // ============================================================ second business, switching, dark mode
    const bctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "ar-LY" });
    const bp = await bctx.newPage();
    watch(bp, "owner-b");
    await bp.goto(`${BASE}/register`);
    await bp.fill("#full_name", "مالك الفرع الثاني");
    await bp.fill("#business_name", "وكالة الفجر للسياحة");
    await bp.fill("#email", `b-${RUN}@fajr.ly`);
    await bp.fill("#password", "owner-b-pass-123");
    await bp.getByRole("button", { name: "إنشاء الحساب" }).click();
    await bp.waitForURL(/\/channels\?welcome=1/);
    await bp.getByRole("link", { name: "الفريق" }).click();
    await bp.selectOption("#inv-role", "admin");
    await bp.fill("#inv-note", "مدير من النور");
    await bp.getByRole("button", { name: "إنشاء رابط دعوة" }).click();
    const link2 = await bp.getByLabel("رابط الدعوة").inputValue();
    ok("second business owner invites an admin (owner-only role option)");

    const dctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, locale: "ar-LY", colorScheme: "dark" });
    const dp = await dctx.newPage();
    watch(dp, "dark");
    await dp.goto(link2);
    await dp.getByRole("link", { name: "سجّل دخولك" }).click();
    await dp.waitForURL(/\/login\?invite=/);
    await dp.getByText("سجّل دخولك لقبول الدعوة").waitFor();
    await dp.fill("#email", OWNER_EMAIL);
    await dp.fill("#password", "wrong-password-1");
    await dp.getByRole("button", { name: "دخول" }).click();
    await dp.getByText("البريد أو كلمة المرور غير صحيحة").waitFor();
    await dp.fill("#password", PASS);
    await dp.getByRole("button", { name: "دخول" }).click();
    await dp.waitForURL(/\/inbox/);
    const tsel = dp.getByLabel("النشاط");
    const names = await tsel.locator("option").allTextContents();
    if (!names.includes("وكالة الفجر للسياحة") || !names.includes(NOOR)) throw new Error(`tenants: ${names}`);
    ok("existing user: wrong password error, then accepts invite via /login?invite= (two businesses)");
    await tsel.selectOption({ label: NOOR });
    await dp.locator(".conv-item").first().waitFor();
    await tsel.selectOption({ label: "وكالة الفجر للسياحة" });
    await dp.getByText("لا توجد محادثات هنا").waitFor();
    await dp.getByRole("link", { name: "طلبات الحجز" }).click();
    await dp.getByText("لا توجد طلبات حجز بعد").waitFor();
    await dp.getByRole("link", { name: "الفريق" }).click();
    await dp.locator(".list-row").filter({ hasText: "مالك الفرع الثاني" }).waitFor();
    if (!(await dp.locator(".sidebar-foot").textContent()).includes("مدير")) throw new Error("role label");
    await shot(dp, "15-dark-team-second-business");
    ok("switch business: other business's inbox/leads are empty, its team shown, role = admin");
    await dp.getByRole("link", { name: "القنوات" }).click();
    const href2 = await dp.getByRole("link", { name: "ربط قناة" }).first().getAttribute("href");
    await dp.goto(`${BASE}${href2}`);
    await dp.locator("#workspace").waitFor({ state: "visible" });
    if ((await dp.locator("#tenant-name").textContent()) !== "وكالة الفجر للسياحة") throw new Error("/connect business");
    ok("/connect preselects the business chosen in the dashboard");
    await dp.goto(`${BASE}/inbox`);
    await dp.getByLabel("النشاط").selectOption({ label: NOOR });
    await dp.locator(".conv-item").first().click();
    await dp.locator(".bubble").first().waitFor();
    await shot(dp, "16-dark-inbox");
    await dp.reload();
    await dp.locator(".bubble").first().waitFor();
    const stored = await dp.evaluate(() => localStorage.getItem("dash.tenant"));
    if ((await dp.getByLabel("النشاط").inputValue()) !== stored) throw new Error("business choice not kept");
    ok("business choice persists across reload; dark mode renders");

    // ============================================================ logout
    await page.goto(`${BASE}/inbox`);
    await page.getByRole("button", { name: "تسجيل الخروج" }).click();
    await page.waitForURL(/\/login/);
    if ((await page.evaluate(() => fetch("/api/v1/me").then((r) => r.status))) !== 401) throw new Error("session alive");
    ok("logout revokes the session");
  } catch (err) {
    console.error("FAIL:", err.message);
    for (const p of browser.contexts().flatMap((c) => c.pages())) {
      try {
        await p.screenshot({ path: path.join(SHOTS, `zz-fail-${Date.now()}.png`) });
      } catch {}
    }
    process.exitCode = 1;
  } finally {
    await failSends(false).catch(() => {});
    await browser.close();
    console.log(`\n${results.length} checks passed`);
    // متوقعة: 401 قبل الدخول/بعد الخروج، و502 المتعمّد، و401 لكلمة المرور الخاطئة
    const unexpected = problems.filter((p) => !/\b(401|502)\b|Unauthorized/.test(p));
    if (unexpected.length) {
      console.log("Unexpected browser problems:\n" + unexpected.join("\n"));
      process.exitCode = 1;
    } else {
      console.log(`browser problems: none unexpected (${problems.length} expected 401/502)`);
    }
  }
})();
