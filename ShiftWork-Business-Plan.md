# ShiftWork — Business Plan, Brand Identity & Launch Pricing

> Master reference document. Owner: Bim (Founder). Marketing lead view.
> Last updated: 18 July 2026. Currency: Thai Baht (฿); USD shown at ≈฿35/$1 for reference.
> Status: pre-launch. Phase 0 = Facebook page + concierge (manual) backend.

---

## 1. Executive summary

ShiftWork turns a head nurse's most dreaded monthly chore — building the ward shift roster by hand — into a few-minute, fair, rule-compliant result. The engine already exists: an OR-Tools CP-SAT solver that generates a monthly nurse schedule from a simple CSV, respecting 19 Thai-nursing-specific constraints and outputting in Thai locale (ช/บ/ด/OFF).

We go to market lean. **Phase 0** is a Facebook page with direct chat; customers send their ward data, we run the solver manually and return the schedule — a "concierge MVP" that validates willingness-to-pay before we build self-serve software. **Phase 1** is a self-serve web app. **Phase 2** expands beyond nursing to any shift-based workforce (manufacturing, hospitality, retail, security, logistics) and beyond Thailand.

The business is subscription-based, priced **per ward** (capped by number of nurses) across a free tier plus four paid tiers — a structure deliberately engineered to convert. A double-sided referral program ("Bring a Ward") drives low-cost, high-retention growth.

The wedge is narrow and winnable: ~1,250 hospitals and ~123,000–158,000 nurses in Thailand, working in an estimated 5,000–10,000 schedulable units, almost all still rostered by hand or in spreadsheets. The long-term prize is the operating system for shift-based teams worldwide.

---

## 2. Brand identity

### 2.1 The one-line story
**ShiftWork gives head nurses their evenings back** — turning a day of roster-juggling into a few clicks, with schedules that are fair, compliant, and humane.

### 2.2 Mission
Eliminate the hidden, unpaid labour of shift scheduling so that the people who care for us can spend their time on care, not on spreadsheets.

### 2.3 Vision
Become the scheduling layer for every shift-based team on earth — starting with the nurses who need it most.

### 2.4 Positioning statement
*For head nurses in Thai hospitals who lose hours every month building shift rosters by hand, ShiftWork is an automated scheduling service that produces fair, rule-compliant monthly schedules in minutes. Unlike spreadsheets or generic global scheduling apps, ShiftWork is built around Thai nursing rules and language, and priced for a single ward's budget.*

### 2.5 Brand values
- **Fairness** — balanced workloads, transparent rules, no favouritism. This is the product's emotional core; every nurse cares whether the roster is fair.
- **Trust** — the schedule is correct and follows the rules, every time.
- **Simplicity** — if a busy head nurse can't use it between patients, we failed.
- **Care** — we serve carers; we design with empathy for exhausted people.
- **Speed** — minutes, not days.

### 2.6 Voice & tone
Warm, plain-spoken, respectful of the user's expertise. We never talk down to clinical staff. Thai-first language for the nursing market, with clean English for later expansion. Confident but not hype-y — we let "you got your weekend back" do the selling, not buzzwords.

### 2.7 Naming
- **Product:** ShiftWork.
- **Tagline (primary):** *Fair shifts. Less work.*
- **Tagline (Thai-friendly alternatives):** "จัดเวรเสร็จใน 5 นาที" (Rosters done in 5 minutes) · "เวรที่ยุติธรรม ทำงานน้อยลง".
- **Tier names:** Free · Ward · Ward+ · Ward Pro · Department (see §8).

### 2.8 Brand do / don't
- **Do** lead with time saved and fairness. **Don't** lead with "AI" or "algorithms" — the benefit sells, not the tech.
- **Do** show real ward-sized examples. **Don't** show enterprise dashboards that intimidate a single head nurse.
- **Do** speak Thai nursing vocabulary correctly (e.g., ลอย = float/flexible day shift). **Don't** machine-translate clinical terms.

### 2.9 Brand identity (full spec)

**Brand personality.** ShiftWork is designed to feel trustworthy, calm, modern, efficient, human-centered, and professional. Unlike traditional hospital software, it should feel as polished as modern SaaS products such as Linear, Notion, or Stripe while maintaining the trust expected in healthcare.

**Brand colors.**

*Primary*

| Color | Hex | Usage |
|---|---|---|
| Deep Navy | #102A43 | Logo, headings, primary text |
| Medical Teal | #14B8A6 | Accent color, buttons, links, highlights |

*Neutral*

| Color | Hex | Usage |
|---|---|---|
| Charcoal | #1F2937 | Body text |
| Light Gray | #E5E7EB | Borders, dividers |
| Off White | #F8FAFC | Backgrounds |

*Semantic*

| Color | Hex | Usage |
|---|---|---|
| Success | #10B981 | Success states |
| Warning | #F59E0B | Warnings |
| Error | #EF4444 | Errors |

**Logo.** The primary logo is a custom typography wordmark: **Shift** in Deep Navy (#102A43), **Work** in Medical Teal (#14B8A6). The logo should never use gradients, shadows, bevels, or decorative effects.

**Typography.** Modern geometric sans-serif; medium to SemiBold weight; tight letter spacing; rounded geometric forms; clean, minimal appearance. Recommended inspirations: Söhne, Circular, Inter, Suisse Int'l.

**Visual language.** The design language should communicate simplicity and confidence: plenty of white space, rounded corners (12–16 px), soft teal gradients used sparingly, flat illustrations and icons, no skeuomorphic effects, minimal iconography, large bold headings, and clean card layouts.

**Photography & graphics.** Avoid generic stock photos, doctors shaking hands, blue medical crosses, busy dashboards, and cartoon illustrations. Prefer modern product UI, abstract flowing shapes, calm gradients, real nursing workflows, and clean interface screenshots.

**Brand voice.** Clear, practical, helpful, respectful, and confident. Avoid marketing buzzwords and exaggerated claims. Focus on solving real scheduling problems for head nurses.

---

## 3. Market analysis (Thailand)

### 3.1 Size of the opportunity
- **Hospitals:** ~1,250 total — ~905 public under the Ministry of Public Health (35 regional, 96 general, 774 community) plus ~349 private hospitals.
- **Nurses:** ~123,000 professional nurses (some sources cite up to ~158,000) — the single largest medical workforce in the country.
- **Beds:** ~150,000.
- **Schedulable units (our real TAM):** at roughly 25–30 beds per ward, Thailand has an estimated **5,000–6,000 hospital wards**, each with a head nurse who builds a monthly roster. Adding clinics, dialysis centres, nursing homes, and private care, the broader pool is plausibly **8,000–10,000 schedulable units**.

Each ward = one head nurse = one buyer = one account. That is the unit of our business.

### 3.2 Willingness to pay
Thai registered nurses earn roughly **฿24,000–38,000/month** (entry-level lower, senior higher); a head nurse sits above that. A head nurse's day is worth on the order of ฿1,000–1,500. Manual monthly rostering eats **half a day to two days** of skilled time and generates conflict when it's perceived as unfair. Our pricing (from ฿390/month) is a small fraction of the value of even one hour saved — a deliberate "no-brainer" position. The buyer, however, often pays from a **ward/department budget**, not personal salary, so we must make the value legible enough to expense.

### 3.3 Pain points we exploit
1. **Time:** hours lost every month to a tedious, error-prone task.
2. **Fairness disputes:** manual rosters breed resentment (who got the night shifts, who got the holidays).
3. **Rule compliance:** Thai nursing rules (senior-nurse day-only, no Evening→Night, meeting days, holiday balance) are hard to satisfy by hand.
4. **Last-minute changes:** one nurse calls in sick and the whole grid must be redone.

### 3.4 TAM / SAM / SOM (illustrative)
- **TAM (Thailand):** ~8,000–10,000 schedulable units.
- **SAM (reachable via Facebook + word-of-mouth in nursing community, paid-capable):** ~3,000–5,000 wards.
- **SOM (realistic 18–24 months):** 300–1,000 paying wards. At a blended ARPU of ~฿600/month, 1,000 paying wards ≈ **฿7.2M ARR (~$205k)**; 3,000 wards ≈ **฿21.6M ARR**. The number is modest in Thailand alone — the upside is the Phase 2 global, multi-industry expansion, where the addressable market is in the millions of shift-based teams.

---

## 4. Problem & solution

**Problem:** Building a fair, rule-compliant monthly shift roster by hand is slow, stressful, and politically charged — and almost every Thai ward still does it manually or in Excel.

**Solution:** ShiftWork ingests the ward's nurses, requests, holidays, and meetings and returns an optimised monthly schedule in minutes — balancing workload, respecting seniority and shift-transition rules, and producing a clean grid in Thai. The output is something a head nurse can post on the wall without an argument.

---

## 5. Product overview

### 5.1 What exists today
- **Engine:** OR-Tools CP-SAT constraint solver (`shiftwork.py`) producing monthly schedules.
- **Inputs:** CSV import (`dataimporter.py`) — nurses, per-nurse shift requests, holidays, meetings, settings.
- **Rules:** 19 documented constraints (`constraint.md`), including senior-nurse day-only/weekend-off rules, configurable shift-transition bans (Evening→Night, Night→Day), meeting-day handling, new-nurse protections, and workload/holiday balancing.
- **Output:** schedule grid in Thai locale (ช = Day, บ = Evening, ด = Night, blank = OFF).
- **Interface:** desktop GUI (`gui.py`) with a background solver thread.

### 5.2 Productisation roadmap
- **Phase 0 (now):** concierge — intake by Facebook chat, solver run manually, schedule returned as file/image.
- **Phase 1:** self-serve web app — upload/enter ward, click generate, download schedule; accounts, billing, the free tier.
- **Phase 1.5:** in-app edits, sick-day re-solve, mobile-friendly view, shareable schedule link for the whole ward.
- **Phase 2:** generalised constraint engine for any industry; multi-language; team/manager roles; integrations (payroll, LINE notifications); international billing.

---

## 6. Business model

Recurring **subscription**, billed monthly or annually, with the **ward** as the price metric (capped by nurse count). This aligns price with value (bigger ward = more time saved = more we capture) and creates natural upgrade pressure as wards grow.

**Phase 0 operations (manual backend):**
- Intake via a standard Facebook/Google form (ward name, nurse list, requests, holidays, settings) to keep concierge runs fast and consistent.
- Defined turnaround SLA (e.g., schedule delivered within 24 hours).
- Payment by PromptPay / bank transfer; track subscriptions in a simple sheet.
- **Protect founder time:** during the manual phase, "Free" is a *one-time sample schedule* (a concierge trial), not an unlimited free tier. The ongoing free self-serve tier launches only once the web app automates delivery (Phase 1) — otherwise every free user costs Bim real hours.

---

## 7. Go-to-market

### 7.1 Phase 0 — Facebook page + concierge (validate)
- Build a Facebook page positioned around the pain ("จัดเวรพยาบาลเสร็จใน 5 นาที"), with before/after roster images and short demo clips.
- Post in Thai nursing groups; reach out to head nurses directly; offer the free sample schedule as the hook.
- Goal: 20–50 paying wards, proof of retention, testimonials, and a refined intake form. Learn the real constraints customers ask for.

### 7.2 Phase 1 — self-serve web app (scale)
- Launch free tier + paid tiers + billing + referral program.
- Convert Phase 0 concierge customers to self-serve; ride their referrals.
- Content + community: a "fair rostering" playbook, nurse-influencer partnerships, hospital in-services.

### 7.3 Phase 2 — multi-industry / global (expand)
- Repackage the engine for generic shift teams; localise; pursue SME segments (clinics → hospitality → manufacturing) and international markets.

### 7.4 First-100-customers tactics
- **Founding Member offer:** first 50–100 wards get a lifetime discount (e.g., 30–40% off forever) in exchange for a testimonial and feedback — creates urgency and a loyal base for referrals.
- Weekly "roster clinic" live sessions in nursing groups.
- Make every delivered schedule shareable (watermarked in free tier) so the product markets itself ward-to-ward.

---

## 8. Packaging & pricing

### 8.1 Pricing philosophy
Charge **per ward**, gated by **number of nurses**, with feature depth rising across tiers. Five reasons this structure maximises the chance the customer pays:

1. **The free tier's nurse cap sits below a real ward.** Typical wards run 15–30 nurses; a free cap of 8 means a real ward must upgrade to actually use it. Free proves value; it doesn't satisfy the need.
2. **Price scales with value, not arbitrarily.** Bigger ward = more time saved, so a larger bill feels fair.
3. **Small steps beat one big price.** ฿390 → ฿690 → ฿1,290 lets buyers self-select; the mid tier looks reasonable next to Pro (classic good-better-best anchoring).
4. **Annual prepay discount** pulls cash forward and cuts churn.
5. **Everything is benchmarked against a no-brainer:** even the entry tier is ≈฿13/day — a fraction of one nurse's hourly wage and far below the ~$5/user/month global tools (NurseGrid, Deputy) would charge a 15-nurse ward (~฿2,600/month).

### 8.2 The tiers (monthly, billed per ward)

| Tier | Price / month | Nurses per ward | Best for | Effective ฿/nurse |
|------|---------------|-----------------|----------|-------------------|
| **Free** | ฿0 | up to 8 | Trying it out, tiny units | — |
| **Ward** | ฿390 | up to 15 | Small wards | ~฿26 |
| **Ward+** | ฿690 | up to 25 | Standard hospital wards | ~฿28 |
| **Ward Pro** | ฿1,290 | up to 40 | Large / complex wards | ~฿32 |
| **Department** | from ฿2,900 (or custom) | multiple wards (~up to 5) | Nursing directors, multi-ward | custom |

**Annual billing:** pay for 10 months, get 12 (≈17% off). Strongly promoted as the default.
**Founding Member (first 50–100 wards):** 30–40% off for life.

> Prices are a recommended starting point, not gospel. Validate in Phase 0: if head nurses say "yes" without hesitation, you're priced too low — test ฿490 / ฿890 / ฿1,490. If you hit resistance, the annual discount and Founding offer are your levers before you cut list price.

### 8.3 Feature matrix

| Capability | Free | Ward | Ward+ | Ward Pro | Department |
|---|---|---|---|---|---|
| Auto-generate monthly schedule | ✓ | ✓ | ✓ | ✓ | ✓ |
| Core Thai constraints (seniority, transitions, balance) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Schedules per month | 1 | Unlimited | Unlimited | Unlimited | Unlimited |
| Nurse shift requests & holidays | Limited | ✓ | ✓ | ✓ | ✓ |
| Meeting-day handling | — | ✓ | ✓ | ✓ | ✓ |
| Clean export (no watermark) | — (watermarked) | ✓ | ✓ | ✓ | ✓ |
| Schedule history & versions | — | — | ✓ | ✓ | ✓ |
| Fairness / workload report | — | — | ✓ | ✓ | ✓ |
| Multi-month planning | — | — | — | ✓ | ✓ |
| Custom constraints | — | — | — | ✓ | ✓ |
| Sick-day re-solve | — | — | ✓ | ✓ | ✓ |
| Multiple wards + director dashboard | — | — | — | — | ✓ |
| Support | Community | Email | Email | Priority | Priority + onboarding |

### 8.4 Unit economics (rule-of-thumb)
At a blended ARPU of ~฿600/month and even a conservative 12-month average retention, LTV ≈ ฿7,200. With referral- and community-led acquisition keeping CAC low (see §9), the model targets an LTV:CAC well above the 3:1 healthy threshold.

---

## 9. Referral campaign — "Bring a Ward"

### 9.1 Design rationale
Your instinct (invite people → earn free months) is right; we sharpen it using what works in SaaS referrals: **double-sided** rewards (lift participation ~85%), **credit-based** rewards (outperform cash by ~18%), and **tiered milestones** (up to ~41% more repeat referrals). Critically, **reward on conversion, not on invites** — paying for 5 invites where only 1 converts wastes margin on 4 non-buyers. So we reframe your "5 invites → 1 free month" as the cleaner, stronger **"each ward you bring that subscribes = 1 free month."**

### 9.2 How it works
- **The friend (referred ward) gets:** their **first month free** on any paid plan (or 50% off the first 2 months) — removes their risk.
- **The referrer gets:** a **฿390 account credit (one free Ward-month) for every ward that subscribes** through their link and completes its first payment. Credits stack and apply to the next invoice.
- **Milestone bonuses (tiered):**
  - 3 paying referrals → free upgrade to the next tier for 2 months.
  - 5 paying referrals → 3 months free at your current tier.
  - 10 paying referrals → 1 year at 50% off, or a ฿3,000 cash reward.

### 9.3 Guardrails (protect margin & prevent abuse)
- Reward triggers **only after** the referred ward's first successful payment **and** ~30 days active (filters churn-gaming).
- New wards/customers only (no self-referral, no re-referring an existing account).
- Credit cap of 12 free months per referrer per year.
- One-click sharing with a pre-written Thai message and a personal referral link.

### 9.4 Economics
Acquiring one paying ward costs at most ~฿780 (1 free month to referrer + 1 to friend) — often less, since the friend's free month is deferred revenue, not cash out. Against an LTV of ~฿7,200+, that's a ~9:1 return, and referred customers churn ~20% less and convert higher. This is your cheapest, highest-quality growth channel; lean into it.

---

## 10. Metrics & KPIs

Track from day one: **Activation** (% of trials that get a usable schedule), **Free→Paid conversion**, **MRR / ARR**, **ARPU**, **Churn & Net Revenue Retention**, **Referral coefficient (K-factor)** and share of new customers from referral, **Concierge turnaround time** (Phase 0 health), and a qualitative **"would you be very disappointed without it?"** survey to confirm product-market fit (target >40% "very disappointed").

---

## 11. Risks & mitigations

- **Concierge doesn't scale / eats founder time** → standard intake form, SLA, batch runs; cap free to a one-time sample until Phase 1 automates.
- **Buyers can't expense it** → provide an invoice/receipt and a one-line ROI ("saves ~X hours/month"); position the Department tier for budget-holders.
- **Trust in an automated roster** → always show the fairness report; let head nurses tweak and re-solve; keep them in control.
- **Low willingness-to-pay** → annual discount + Founding offer before cutting list price; emphasise time-and-conflict savings, not features.
- **Global incumbents (Deputy, Connecteam, NurseGrid) move down-market** → defend with Thai-specific rules, language, local payment, price, and nursing-community trust; move fast on Phase 2.
- **Single-founder bus factor / manual backend errors** → templatise, checklist every concierge run, prioritise Phase 1 automation.

---

## 12. Step-by-step launch playbook

This is the concrete "do this" checklist for getting ShiftWork live as a Facebook page + concierge (manual) service. Work top to bottom; most of Stage A can be done in a week.

### Stage A — Foundation (before you tell anyone)

1. **Set up how you get paid.** Register a PromptPay QR (personal or business) and/or a bank account for receiving subscriptions. Decide how you'll issue a simple receipt/invoice (a templated PDF or image is fine at first).
2. **Lock the offer.** Confirm the launch tiers, the annual "pay 10 get 12" deal, and the **Founding Member** discount (30–40% off for life, first 50–100 wards). Write the payment instructions in one short paragraph you can paste into chat.
3. **Create minimal brand assets.** Logo (even a clean wordmark), colour palette, a Facebook profile image and cover banner with the tagline *"จัดเวรพยาบาลเสร็จใน 5 นาที"* (Rosters done in 5 minutes), and a one-line pitch.
4. **Build the intake form.** A Google Form (or Google Sheet template) that collects everything the solver needs: ward name, nurse list with roles/seniority, per-nurse shift requests, holidays, meeting days, and the setting flags (allow E→N, allow N→D, head-nurse special shift). This is what makes concierge fast and error-free.
5. **Write your concierge run-sheet.** Document the exact steps: receive form → load CSV → run `shiftwork.py` → sanity-check the grid → export as image + CSV (+ PDF) → send to customer → send payment request → log it. Set a **turnaround SLA of 24 hours** and put it in writing.
6. **Make one killer demo.** A before/after image (messy Excel roster vs. clean ShiftWork grid) and a 60-second screen recording of a schedule being generated. This single asset will do most of your selling.
7. **Run 2–3 free pilots.** Find a few friendly head nurses, do their next month's roster for free, and turn the results into your **first testimonials and before/after proof**. Fix any workflow snags now, while stakes are low.

### Stage B — Go live

8. **Publish the Facebook page** with the pitch, the demo, a plain-language "how it works" (3 steps), pricing, and one clear call-to-action: *"Inbox us your ward and get a free sample schedule."*
9. **Queue 5 launch posts** so the page looks alive: (a) the pain-point hook, (b) the demo video, (c) a pilot testimonial, (d) how-it-works, (e) pricing + Founding Member offer with urgency ("first 50 wards only").
10. **Distribute where head nurses already are.** Post/share in Thai nursing Facebook groups, message head nurses directly, and ask your pilot users to share to their networks. Personal outreach converts best at this stage.
11. **Start a simple CRM sheet.** One Google Sheet: lead name, ward, hospital, status (lead → sample sent → subscribed), tier, payment date, referral source. You cannot manage what you don't track.

### Stage C — Convert & operate

12. **Use chat scripts.** Prepare short, friendly Thai reply templates for the common moments: welcome + how to start, pricing, "send me your ward via this form", delivering the schedule, and asking for payment. Consistency saves hours.
13. **Deliver, then collect.** Send the finished schedule, then the PromptPay request and receipt. Keep the 24-hour SLA — reliability is your reputation in a tight-knit nursing community.
14. **Ask for the referral every time.** The moment a customer is happy, invite them into **"Bring a Ward"** (§9): their friend gets a free first month, they get a free month per paying referral. Give them the pre-written share message.

### Stage D — Iterate toward Phase 1

15. **Watch the KPIs** (§10): sample→paid conversion, retention, turnaround time, and share of new customers from referral. Note the constraints/features customers ask for most.
16. **Automate when it hurts.** When manual runs start capping your time (~20–50 active wards), that's your signal to build the Phase 1 self-serve web app — funded and de-risked by paying customers who already want it.

---

## 13. 90-day action plan

1. **Weeks 1–2:** finalise brand (logo, palette, page), build the intake form, lock Phase 0 pricing and the Founding Member offer.
2. **Weeks 2–4:** launch Facebook page; post before/after rosters and a 60-sec demo; join and contribute in Thai nursing groups.
3. **Weeks 3–8:** run concierge for the first 20–50 wards; collect testimonials; refine constraints customers actually ask for; measure turnaround and conversion.
4. **Weeks 6–10:** soft-launch "Bring a Ward" with Phase 0 customers manually.
5. **Weeks 8–12:** validate pricing (test higher anchors); decide Phase 1 web-app scope; line up the founding cohort to migrate to self-serve.

---

## 14. Appendix — assumptions & sources

**Key assumptions:** ~25–30 beds/ward → 5,000–10,000 schedulable units; blended ARPU ~฿600/month; FX ≈ ฿35/$1; retention ≥12 months. All pricing figures are starting hypotheses to validate in Phase 0.

**Sources (market data, July 2026):**
- Thailand hospital & bed counts: [Statista — beds in public/private hospitals](https://www.statista.com/statistics/1480091/number-of-beds-in-public-and-private-hospitals-thailand/), [Wikipedia — List of hospitals in Thailand](https://en.wikipedia.org/wiki/List_of_hospitals_in_Thailand), [Thailand Healthcare Industry Outlook 2025–2030](https://www.intellifyglobal.com/thailand-healthcare-industry-outlook-2025/)
- Healthcare workforce / nurse counts: [Statista — healthcare professionals by type](https://www.statista.com/statistics/1185049/thailand-number-of-healthcare-professional-by-type/), [PMC — health workforce distribution in Thailand](https://pmc.ncbi.nlm.nih.gov/articles/PMC6368115/)
- Nurse salaries: [PayScale — RN Thailand](https://www.payscale.com/research/TH/Job=Registered_Nurse_(RN)/Salary), [ERI — RN Thailand](https://www.erieri.com/salary/job/registered-nurse/thailand), [JobsDB — RN salary](https://th.jobsdb.com/career-advice/role/registered-nurse/salary)
- Competitor pricing: [Connecteam — healthcare staff scheduling](https://connecteam.com/healthcare-staff-scheduling-software/), [Connecteam — nurse scheduling](https://connecteam.com/top-nurse-scheduling-software-solutions/), [FitSmallBusiness — nurse scheduling software](https://fitsmallbusiness.com/nurse-scheduling-software/)
- Referral benchmarks: [GrowSurf — SaaS referral statistics](https://growsurf.com/statistics/saas-referral-statistics/), [Impact.com — SaaS referral program guide](https://impact.com/referral/saas-referral-program-guide/), [ReferralRock — SaaS referral programs](https://referralrock.com/blog/starting-your-saas-referral-program/)
