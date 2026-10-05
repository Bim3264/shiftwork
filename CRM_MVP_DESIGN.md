# ShiftWork CRM — MVP Design

Status: **draft for review** · Scope: internal tool for tracking ShiftWork's own customers (hospitals, wards, contacts) — reuses the existing web app stack. · Companion to `WEB_MVP_DESIGN.md`.

---

## 1. Purpose & scope

Give the ShiftWork team one place to track **who the customers are, where each one is in the funnel, and every conversation with them** — from a cold lead to a paying ward to a churn risk. Today this lives in your head, chat threads, and a spreadsheet; this replaces that with a small, structured tool that sits *next to* the product data so a customer record can point straight at the ward they actually run schedules in.

It is an **internal, staff-only** tool. It is not customer-facing and has nothing to do with the nurse-facing product UI.

**In scope (MVP):**

- **Customers** (accounts) — a hospital / institution, with lifecycle stage and attribution.
- **Contacts** — the people at that customer (head nurse, ward admin, nursing director, procurement).
- **Interactions** — a timeline of notes, calls, LINE chats, demos, and trial setups per customer.
- **Subscription snapshot** — what tier / price each customer's ward(s) are on, linked to the real product `Ward` where one exists.
- **Follow-up tasks** — a light reminder ("call back Friday", "trial ends in 3 days").
- **Referral tracking** — the "Bring a Ward" program: who referred whom, and the ฿390 credit owed.
- **A one-screen dashboard** — stage counts, MRR from active subscriptions, trials ending soon, tasks due.

**Out of scope (MVP — deferred to "grow later," see §12):** automated email/LINE inbox sync, automatic reminders/notifications, billing (PromptPay) integration that auto-fills subscriptions, custom fields, bulk import/export, reporting beyond the single dashboard, and any multi-user notification/assignment workflow beyond a simple owner field.

---

## 2. Guiding principles

1. **Reuse the ShiftWork stack, don't start a second app.** FastAPI + Jinja + HTMX + SQLAlchemy, SQLite→Postgres, Google sign-in — exactly what the product web app already runs. The CRM is a new package inside the same app and the same database, not a separate service. That is the single biggest reason to build it here rather than buy a generic CRM: it can *join customer records to product data*.

2. **A customer is a `crm_org`; a billable unit is a `Ward`.** Your pricing is already per-ward, and a hospital can run several wards (that's what the *Department* tier is). So the CRM keeps the buying institution (`crm_org`) separate from the product tenant (`Ward`), and links them. One hospital → many wards → many subscriptions.

3. **Everything hangs off the customer.** Contacts, interactions, subscriptions, tasks, and referrals all reference a `crm_org`. The customer detail page is the whole product — a table on the left, one rich page on the right.

4. **Log-and-move-on beats data entry.** The core loop is "I just talked to a customer → log one line → done," using the same HTMX add-fragment pattern the solve-status page already uses. If logging an interaction takes more than a few seconds, no one will use it.

5. **Staff-only, additive to the schema.** The CRM lives behind the existing admin gate and adds *new* tables (`crm_*`); it never modifies the product tables. Productizing / migrating later stays mechanical.

---

## 3. Where it lives (architecture)

The CRM is a module in the existing web app, sharing its process, database, and auth:

```
                         ┌──────────────────────────────────────────────┐
      Google OIDC ────── │  ShiftWork web app (FastAPI + Jinja + HTMX)   │
   (existing staff)      │                                              │
                         │   /app/...   product routes (schedules)      │
   Staff browser ──────▶ │   /crm/...   NEW: CRM routes (admin-only)    │
        ▲   HTMX         │                                              │
        │  add-note,     └───────────────┬──────────────────────────────┘
        │  poll, filter                  │ read/write
        │                                ▼
        │                     ┌────────────────────────────┐
        └── timeline/list ◀───│  Database (SQLite → PG)     │
                              │                            │
                              │  users, wards, ...  ◀──────┼── existing product tables
                              │  solve_jobs                │        ▲
                              │                            │        │ FK link
                              │  crm_org, crm_contact,     │        │
                              │  crm_interaction,          │────────┘
                              │  crm_subscription,         │  crm_subscription.ward_id
                              │  crm_task, crm_referral    │
                              └────────────────────────────┘
```

No new processes, no new infrastructure. The only new things are: a `webapp/crm/` package (models are added to the existing `webapp/db/models.py` or a `crm_models.py` imported by the same `Base`), a set of `/crm/...` routes gated to `role == "admin"`, and a handful of Jinja templates + HTMX fragments.

Because the CRM and the product share one `Base`/database, a `crm_subscription` row can carry a real `ward_id` foreign key into the product `wards` table — which is what makes the "is this paying customer actually generating schedules?" question answerable (see §8).

---

## 4. Tech stack

Identical to the web MVP — that's the point.

| Concern | Choice | Note |
|---|---|---|
| Web framework | **FastAPI** (existing app) | New `/crm` router mounted on the same app. |
| Templating / interactivity | **Jinja2 + HTMX** | Same server-rendered, add-fragment pattern as the solve page. |
| Database | **SQLite → Postgres** | Same DB as the product; `crm_*` tables added via the same `Base.metadata`. |
| ORM / migrations | **SQLAlchemy** (+ Alembic at product phase) | New models next to the existing ones. |
| Auth | **Existing Google OIDC + session** | CRM reuses it; gated to `role == "admin"`. |
| Background jobs | *none needed for MVP* | CRM has no long-running work; reminders are computed on page load, not queued. |

---

## 5. Data model

New tables, all prefixed `crm_`. Fields marked *(product-phase)* are noted but not built for MVP.

### 5.1 Entity-relationship overview

```
   users (existing) ──owner──┐
                             ▼
  crm_org (customer / hospital)
     │  1─────────────* crm_contact         (people at the customer)
     │  1─────────────* crm_interaction     (timeline: notes/calls/demos)   ── author → users
     │  1─────────────* crm_task            (follow-ups)                     ── assignee → users
     │  1─────────────* crm_subscription    (what they pay) ── ward_id ──▶ wards (existing, nullable)
     │
     ├─ referred_by ◀── crm_referral ──▶ refers  (org ↔ org, "Bring a Ward")
```

### 5.2 `crm_org` — the customer (account)

The buying institution. One row per hospital / clinic / department you're selling to.

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `name` | str | Hospital / institution name (may hold Thai text, like your clinical docs). |
| `kind` | enum | `hospital` \| `clinic` \| `department` \| `individual` (default `hospital`). |
| `city` | str | |
| `province` | str | |
| `country` | str | Default `Thailand`. |
| `lifecycle_stage` | enum | `lead` \| `contacted` \| `trial` \| `active` \| `churned` \| `lost` (see §6). |
| `source` | enum | `inbound` \| `referral` \| `outbound` \| `event` \| `other` — attribution. |
| `owner_id` | FK→users | The staff member who owns the relationship. |
| `is_founding_member` | bool | The "first 50–100 wards, 30–40% off for life" flag. |
| `line_id` | str | LINE is the primary channel in the Thai market; keep it first-class. |
| `website` | str | nullable |
| `summary` | text | One free-text paragraph: who they are, current state. |
| `created_by` | FK→users | |
| `created_at` / `updated_at` | datetime | |

### 5.3 `crm_contact` — a person at the customer

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `org_id` | FK→crm_org | |
| `name` | str | |
| `role_title` | str | e.g. Head Nurse, Ward Admin, Nursing Director, Procurement. |
| `is_primary` | bool | The main point of contact. |
| `email` | str | nullable |
| `phone` | str | nullable |
| `line_id` | str | nullable |
| `notes` | text | nullable |
| `created_at` | datetime | |

### 5.4 `crm_interaction` — the timeline

Every touchpoint. This is the table that gets written to most often, so it stays tiny and fast to add.

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `org_id` | FK→crm_org | |
| `contact_id` | FK→crm_contact | nullable (may be a general note). |
| `author_id` | FK→users | Who logged it. |
| `kind` | enum | `note` \| `call` \| `email` \| `line` \| `meeting` \| `demo` \| `trial_setup`. |
| `occurred_at` | datetime | Defaults to now; editable for back-dating. |
| `body` | text | What happened. |
| `created_at` | datetime | |

### 5.5 `crm_subscription` — what they pay (product link)

The bridge between the CRM and the product. One row per ward the customer runs. In the MVP these are **entered by hand** (you record what tier a customer is on); the `ward_id` link is optional and points at the real product `Ward` once they're live.

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `org_id` | FK→crm_org | |
| `ward_id` | FK→wards | **nullable** — links to the actual product tenant when it exists. |
| `tier` | enum | `free` \| `ward` \| `ward_plus` \| `ward_pro` \| `department`. |
| `billing_cycle` | enum | `monthly` \| `annual`. |
| `status` | enum | `trialing` \| `active` \| `past_due` \| `canceled`. |
| `price_thb` | int | Monthly price actually paid (after founding-member discount). |
| `started_at` | date | |
| `renews_at` | date | nullable — powers "trials/renewals ending soon". |
| `canceled_at` | date | nullable |

MRR = sum of `price_thb` (normalizing annual to monthly) across `status = active`. Trials ending soon = `status = trialing` with `renews_at` within N days.

### 5.6 `crm_task` — follow-up reminder (light)

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `org_id` | FK→crm_org | |
| `assignee_id` | FK→users | |
| `title` | str | e.g. "Send Ward+ quote". |
| `due_date` | date | |
| `done` | bool | |
| `created_at` | datetime | |

No scheduler/notifications in MVP — the dashboard simply lists tasks with `done = false` and `due_date <= today`.

### 5.7 `crm_referral` — the "Bring a Ward" program

Directly models the GTM offer ("friend gets 1st month free · you get ฿390 credit per paying referral").

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `referrer_org_id` | FK→crm_org | Who referred (existing customer). |
| `referred_org_id` | FK→crm_org | The new lead they brought in. |
| `status` | enum | `pending` \| `signed` \| `credited`. |
| `first_month_free_applied` | bool | The referred party's perk. |
| `credit_thb` | int | Default 390 — owed to the referrer once the referred customer pays. |
| `created_at` / `credited_at` | datetime | |

---

## 6. Customer lifecycle (the pipeline)

`crm_org.lifecycle_stage` is the funnel. Kept to six stages so the whole pipeline reads at a glance:

```
  lead ─▶ contacted ─▶ trial ─▶ active ──▶ churned
    │                            ▲            │
    └────────── lost ◀───────────┘            │  (re-activation loops back to active)
                                              ▼
```

| Stage | Meaning | Typical exit |
|---|---|---|
| `lead` | Identified, not yet reached. | You make contact → `contacted`. |
| `contacted` | Talked to them; interest unclear/qualifying. | They agree to try → `trial`; or → `lost`. |
| `trial` | On a free/trial tier, evaluating. | They start paying → `active`; or → `lost`. |
| `active` | Paying customer, ≥1 active subscription. | They cancel → `churned`. |
| `churned` | Was paying, now canceled. | Win-back → `active`. |
| `lost` | Never converted. | (terminal) |

Stage is a manual field the owner sets; it is *not* auto-derived from subscriptions in the MVP (that automation is a "grow later" item), but the detail page surfaces the mismatch — e.g. an `active` org with no `active` subscription — as a gentle warning so records don't rot.

---

## 7. Screens (Jinja + HTMX)

Five screens, all server-rendered, all admin-gated under `/crm`.

**7.1 Customers list — `/crm`**
A filterable table: name, stage (colored chip), tier(s), owner, city, last-activity date. Filters (as HTMX query params, no full reloads): by stage, by owner, by source, plus a name search box. A "＋ New customer" button opens a small create form. This is the daily home screen.

**7.2 Customer detail — `/crm/org/{id}`**
The heart of the app, one page:
- **Header** — name, stage chip (click to change stage inline via HTMX), source, owner, founding-member badge, LINE id.
- **Contacts panel** — the people, primary contact starred; add/edit inline.
- **Subscriptions panel** — each ward's tier / price / status / renewal date, with a link to the real product ward when `ward_id` is set (and, if linked, its last solve date — see §8).
- **Interaction timeline** — reverse-chronological notes/calls/demos. A single always-visible "log an interaction" box (kind + body) posts an HTMX fragment that prepends the new entry — the same pattern as the solve-status poll-and-swap. This is the most-used control in the whole tool.
- **Tasks panel** — open follow-ups for this customer; add / tick done inline.
- **Referrals** — who this customer referred, and status of each.

**7.3 Pipeline board — `/crm/pipeline`** *(nice-to-have within MVP)*
A kanban-style column-per-stage view of all customers, so the funnel is visible at a glance. Cards show name + owner + tier. Read-oriented in MVP (drag-to-move-stage is optional; the detail page already changes stage).

**7.4 Tasks — `/crm/tasks`**
A flat list of open follow-ups across all customers, sorted by due date, filterable by assignee. Tick to complete.

**7.5 Dashboard — `/crm/dashboard`**
One screen of numbers computed on load (no jobs, no cache):
- Customers by stage (the funnel counts).
- **MRR** = Σ active `price_thb` (annual normalized to monthly).
- Trials / renewals ending in the next 7 days.
- Tasks due today / overdue.
- Referral credits owed (`crm_referral.status = signed`, not yet `credited`).

---

## 8. Integration with the product (the reason to build in-house)

Because the CRM shares the database with the scheduler, three joins turn it from a contact list into a customer-success tool:

1. **Customer → real ward.** `crm_subscription.ward_id` → `wards.id`. From a customer record you can open the actual product tenant.
2. **Engagement / churn signal.** Join a linked ward to its `solve_jobs` to show *last schedule generated*. An `active`, paying customer whose ward hasn't run a solve in 30+ days is the clearest churn-risk flag you'll get — surface it as a badge on the customer and a list on the dashboard. (Read-only query; no product code changes.)
3. **Referral ↔ credit.** `crm_referral` records the "Bring a Ward" credits so they're not lost in chat; when billing integration lands later, crediting can be automated.

None of these require touching the solver or the product tables — they're all *reads* from the CRM side plus the one nullable FK column.

---

## 9. Permissions & access

- **Staff-only.** Every `/crm` route requires an authenticated user with `role == "admin"` (the existing `User.role`). Regular product users (ward members) get 404/redirect.
- **Owner is a label, not a lock.** In the MVP any admin can see and edit any customer; `owner_id` is for filtering and accountability, not row-level security. Per-owner restrictions are a later addition if the team grows.
- **Customer data sensitivity.** Contacts hold names, emails, phone, LINE ids — business contact data, kept in the same DB behind the same auth as the product. No health or nurse-personal data lives in the CRM; it references *institutions and their staff contacts*, not patients or scheduled nurses.

---

## 10. Reporting (MVP = one dashboard)

Everything in §7.5, computed with plain SQL aggregates on page load. No warehouse, no charts library required — counts and a couple of sums render fine as server-side numbers/tables. A proper dashboard with trend charts is a "grow later" item; the MVP just needs the five living numbers a founder checks daily.

---

## 11. Build phases

A sequence that ships something usable at the end of each step:

- **Phase 0 — schema.** Add `crm_org`, `crm_contact`, `crm_interaction` to `Base`; `create_all` picks them up (Alembic at product phase). Seed a couple of real customers by hand.
- **Phase 1 — the core loop.** Customers list + customer detail + interaction timeline (log a note). This alone replaces the spreadsheet.
- **Phase 2 — money & product link.** `crm_subscription` (+ nullable `ward_id`), the subscriptions panel, and the dashboard's MRR / stage counts / last-solve churn signal.
- **Phase 3 — follow-through.** `crm_task` + tasks screen, and `crm_referral` + referral tracking on the detail page and dashboard.
- **Phase 4 (optional, still MVP-ish).** Pipeline board.

Phases 0–1 are the minimum that's worth using; 2–3 are what make it *ShiftWork's* CRM rather than a generic contact list.

---

## 12. Deferred — "grow later"

Explicitly out of the MVP, in rough priority order:

- **Automated reminders / notifications** (trial-ending, task-due) via email or LINE — needs a scheduler; the MVP shows them on the dashboard instead.
- **Billing (PromptPay) integration** to auto-populate `crm_subscription` and auto-settle referral credits — MVP enters these by hand.
- **Auto-derive `lifecycle_stage`** from subscription status (with manual override).
- **Inbound email / LINE capture** logged automatically as interactions.
- **Custom fields, tags, bulk CSV import/export.**
- **Trend charts & cohort/retention reporting** beyond the single dashboard.
- **Per-owner row-level permissions** and assignment/notification workflow.
- **Self-serve / product-led signup** feeding leads straight into `crm_org` as `source = inbound`.

---

## Appendix — open questions for you

1. **One customer, many wards:** confirmed that a hospital on the *Department* tier is one `crm_org` with several `crm_subscription`/`ward` rows (vs. one org per ward)? The design assumes the former.
2. **Currency/locale:** all pricing in THB, dates in Asia/Bangkok — assumed. Multi-currency is not modeled.
3. **Do you want the pipeline board (7.3) in the first cut, or is the filtered list (7.1) enough to start?**
4. **Should `owner_id` restrict visibility later, or stay a pure label?** (MVP keeps it a label.)
