# ShiftWork — Feature Gaps

Everything in `ShiftWork Wireframes.dc.html` (turn 1) that has no backend, route, or
data model behind the current code. Turn 0 of that file is the app as it exists today.

Source of truth for "exists today": `webapp/db/models.py`, `webapp/web/app.py`,
`webapp/web/templates/*`, `webapp/core/tiers.py`.

Wireframe ids (`1a`, `1k`, …) refer to options in `ShiftWork Wireframes.dc.html`.

---

## 1. Data model gaps

| Gap | Today | Needed for |
|---|---|---|
| **No Nurse table** | Nurses live only inside `schedule_inputs.data` JSON | `1v`, `1w` — roster CRUD, positions, leave status (ลาคลอด) |
| **No Request entity** | Requests are cell tokens in the grid | `1k`, `1l`, `1u` — reason text, submitted-by, approved/denied state |
| **No Schedule/month entity** (dashboard now *derives* month rows — `core/months.py`) | A month is `ward_meta.month` inside an input | `1a`, `1b` — one row per month; status vocabulary (draft / running / done / infeasible) |
| **No user ↔ nurse link** | `ward_members` maps users to wards; nothing maps a user to a nurse row | `1l` (nurse submits own request), `1q` (send schedule to a nurse) |

---

## 2. Missing routes / logic

- ~~**Duplicate last month** (`1f`)~~ — **DONE**. `POST /inputs/{id}/duplicate` via `core/duplicate.py` (roster + settings kept, requests cleared, dates recomputed).
- ~~**Pre-solve validation** (`1i`)~~ — **DONE**. `core/presolve.py` checklist on
  Check/Solve; sound errors block, quota/meeting/senior warnings don't. Not yet: live
  per-cell highlighting and the "upgrade" call-to-action.
- **Infeasibility diagnosis** (`1s`, `1u`) — solver returns `INFEASIBLE` + a log.
  Naming the conflicting day/constraint needs an unsat-core or assumption-based pass.
- **Pre-run relaxations** (`1t`) — needs N solver runs with different relaxations,
  then a comparison of outcomes.
- **Progress + cancel** (`1m`, `1n`) — CP-SAT gives no progress signal; no cancel
  endpoint; no elapsed timer.
- **Best-so-far streaming** (`1o`) — needs a solution callback writing intermediate grids.
- ~~**Fairness stats** (`1p`)~~ — **DONE**. `core/fairness.py` computes per-nurse
  Day/Eve/Night/total/off/weekend counts + senior-aware spread from the stored
  `result_grid`; rendered in `status_fragment.html`. No DB change; retroactive.
- **Publish / share** (`1r`) — no publish state, no share links.
- **Notifications** (`1n`, `1p`) — no LINE, email, or push anywhere.
- **Excel / PDF export** (`1p`, `1r`) — only CSV with a UTF-8 BOM.
- **Ward creation and switching** (`1c`) — `Ward` supports many rows, but only
  `get_or_create_default_ward` runs; no create/switch route.
- **Blank-grid row editing** (`1v`) — code comments admit this is minimal; adding
  nurses in-browser isn't implemented.
- **Billing / upgrade** (`1i`) — tiers are read-only labels derived from config.
  No plan, no payment, no upgrade path.
- **Calendar holiday picker** (`1e`, `1h`) — auto-fill and `holidays_preview` exist
  server-side, but there's no clickable calendar.

---

## 3. Cross-cutting

- **No mobile layout.** `base.html` has a fixed `max-width: 960px` and no media
  queries. Every mobile wireframe (`1c`, `1l`, `1r`) is new work.
- **No Thai UI.** Interface copy is English; only shift tokens are Thai. All Thai
  labels in the wireframes need an i18n layer.
- **No design system.** One 30-line `<style>` block with browser-default tables.
  Everything visual is greenfield.

---

## 4. Suggested order

Cheapest high-value first — all four use data you already have:

1. ~~Month-centric dashboard~~ (DONE, derived view)
2. ~~Duplicate last month~~ (DONE)
3. ~~Pre-solve validation~~ (DONE)
4. ~~Fairness stats on the result screen~~ (DONE)

Then, in rough order of effort:

5. Nurse table + roster CRUD
6. Request entity + approve/deny
7. Mobile layout + Thai i18n
8. Infeasibility diagnosis
9. Notifications, publish/share, exports
10. Billing / tier upgrade
11. Pre-run relaxations, best-so-far streaming (heaviest solver work)

---

## 5. Known bugs (found 2026-09-26)

- ~~**Double-shift requests lose a shift**~~ — **FIXED 2026-09-27** (`test_double_shift`). Was: `DataImporter.transform()` emits `ช/บ` as
  DAY + EVENING entries that the `{day: shift}` merge collapses to EVENING only;
  `ด/บ` emits nothing. Fix = own spec (solver-output change); update
  `presolve._TOKEN_SHIFTS/_TOKEN_ENTRIES` in the same change.
