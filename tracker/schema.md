# Application Tracker — schema + setup

## Recommended platform: Google Sheets (free, fast, good filters)

Alternatives: Airtable (free up to 1k rows, better views), Notion (slower but integrates with notes).

## Schema (16 columns)

| # | Column | Type | Values | Notes |
|---|---|---|---|---|
| 1 | `company` | text | "Wintermute" | Canonical name |
| 2 | `category` | enum | `crypto_mm` / `broker_tech` / `eu_prop` / `africa_treasury` / `fintech_generalist` / `other` | Matches outreach templates |
| 3 | `hq` | text | "London, UK" | City, country |
| 4 | `role_focus` | text | "Trading Systems Engineer" | Specific role title or area |
| 5 | `role_url` | url | https://... | Link to JD or careers page |
| 6 | `priority` | enum | `A` / `B` / `C` | A = top fit, B = good, C = stretch/long-shot |
| 7 | `recruiter_name` | text | "Sarah Chen" | If found |
| 8 | `recruiter_contact` | text | email or LinkedIn URL | Channel + identifier |
| 9 | `engineer_name` | text | "James K." | IC engineer on relevant team for LinkedIn DM |
| 10 | `engineer_linkedin` | url | https://linkedin.com/in/... | DM target |
| 11 | `template_used` | enum | `crypto_mm` / `broker_tech` / `eu_prop` / `africa_treasury` / `fintech_generalist` / `custom` | Which outreach template |
| 12 | `applied_date` | date | 2026-06-02 | YYYY-MM-DD |
| 13 | `status` | enum | `to_apply` / `applied` / `followed_up` / `replied` / `screen_scheduled` / `screen_done` / `onsite` / `offer` / `rejected` / `ghosted` / `withdrew` | Pipeline stage |
| 14 | `follow_up_date` | date | 2026-06-09 | Set to applied_date + 5 business days |
| 15 | `notes` | text | "Recruiter on PTO until June 8" | Any context |
| 16 | `last_contact` | date | 2026-06-09 | Most recent inbound/outbound |

## Google Sheets setup steps

1. Create a new Sheet, name it `Job Search Tracker — Svenn`.
2. Row 1 = column headers (copy from the table above).
3. Format:
   - Column 6 (priority): conditional formatting — A=green, B=yellow, C=grey
   - Column 13 (status): conditional formatting — replied/screen=green, ghosted/rejected=red, applied=blue
   - Column 14 (follow_up_date): conditional formatting — turn red if `<= TODAY()` and status is `applied` (signals follow-up needed)
4. Add filter views:
   - **"To apply this week"** — filter status = `to_apply`, sort by priority A→C
   - **"Need follow-up"** — filter status = `applied` AND follow_up_date <= today
   - **"Active pipeline"** — filter status IN (`replied`, `screen_scheduled`, `screen_done`, `onsite`)
   - **"Wins"** — filter status = `offer`
5. Pin column 1 (company name) for easier scrolling.

## Import the seed list

Use `docs/application_tracker_seed.csv` (next file). In Sheets: File → Import → Upload → Replace current sheet (or paste row by row).

## Weekly cadence

- **Monday**: review "To apply this week" filter, prioritize 10 sends. Customize each from `cold_outreach_templates.md`. Send.
- **Wednesday**: review "Need follow-up" filter. Send one follow-up per stalled application (5 business days post original).
- **Friday**: review "Active pipeline". Update status from any inbound. Note any prep work needed for upcoming screens.
- **Once/month**: cull `ghosted` rows older than 60 days unless company is on A-list.

## Pipeline math (for sanity)

| Stage | Realistic conversion rate | Cumulative from 100 applies |
|---|---|---|
| Apply → reply | 5-12% | 5-12 replies |
| Reply → screen | 60% | 3-7 screens |
| Screen → onsite | 40% | 1-3 onsites |
| Onsite → offer | 30-50% | 0-1 offer per 100 |

So 1 offer per ~100 applications, at the low end. Realistically you want **200-400 applications** in flight over the next 8-12 weeks to land 1-2 offers. The target list below seeds the first 100.
