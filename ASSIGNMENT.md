# Orchestra Ideal Customer Profile and Targeting System

## The short version

You gave a starting Ideal Customer Profile and said it could be improved. It could, and I did. I kept what your evidence supported and changed what it did not, and every change is backed by your own customers or by research on your competitors.

The core finding: **your ideal customer is not an industry or a company size. It is a shape.** A lean data team, 8 to 20 people, on Snowflake or Databricks, that has outgrown its orchestrator and has no platform engineer to run one.

I then built a system that finds these companies automatically, and scored a real list of **240 qualified companies**.

**The full list is here: [Orchestra Target Accounts (Google Sheet)](https://docs.google.com/spreadsheets/d/18xjs1mfUL3_t7KrMAW-CXX19ZmU1NM7FJ9EQN1hfWIU/edit?usp=sharing)**

The reasoning behind each decision, and the customer data it came from, is in [DECISIONS.md](./DECISIONS.md).

---

## What I changed from your starting Ideal Customer Profile

| # | Your starting point | What I changed it to | Why |
|---|---|---|---|
| 1 | Target manufacturing and similar industries | Target the **data-team shape**, industry second | Your customers span ceramics, logistics, construction, energy and software. They share a shape, not an industry. |
| 2 | 1,000 to 10,000 employees | Company size is a **weak signal** | CoorsTek has 5,000 employees, Workpath has 37. Both are your customers. The data team is what they share. |
| 3 | (no clear metric given) | **Data-science team size** is the real signal | Every customer except Experian runs a data team of 0 to 11 people. Experian at 602 is a showcase logo, not the pattern. |
| 4 | Manufacturing is a good fit | Manufacturing **with the shape** only | Not all manufacturers. SAP, SSIS and Microsoft Fabric locked shops are excluded, there is nothing to orchestrate. |
| 5 | Fit only | Split the profile by **revenue, not just fit** | Current customers prove fit but many sit below a healthy deal size. I separated fit-and-pays from fit-only. |

Snowflake stays primary, Databricks second, exactly as you said. The evidence backed that fully.

---

## Question 1

> **The fields you will fetch and data points you will collate that constitute Orchestra's Ideal Customer Profile. What do you think our Ideal Customer Profile should be? How would you refine it?**

The Ideal Customer Profile is a **lean data team, not a type of company.**

The buyer is a data team of roughly 1 to 20 people, on Snowflake or Databricks with dbt, that has outgrown a self-hosted or legacy orchestrator and has no dedicated platform engineer to run one. The champion is a hands-on Data Architect, Head of Data, or Data and Insights Manager, not a Chief Data Officer.

I split it into three bands, because fit and revenue are not the same thing:

| Tier | Data team size | What it means | Companies found | Pipeline value |
|---|---|---|---|---|
| **Must-Win** | 8 to 20 | Fit and revenue overlap. Lead here. | **85** (57 US, 28 UK) | **~1.7M to 3.4M** at 20k to 40k deal size |
| **Stretch** | 21 to 40 | Higher deal size, harder to win. Land and expand. | **41** (30 US, 11 UK) | Upside |
| **Self-Serve** | 3 to 7 | Real fit, lower deal size. Keep as inbound. | **114** (80 US, 34 UK) | Volume |
| **Excluded** | 0 to 2, or 40+ with a platform team | No team, too mature, or legacy locked | Filtered out | Not targeted |

**The fields I collect per company** to make this decision:

1. Company name, domain, country, industry, employee count, revenue
2. **Data-science team size** (the primary signal)
3. Engineering and IT headcount (context)
4. Warehouse: Snowflake, Databricks, or other
5. dbt in use, yes or no
6. Self-hosted Airflow or other orchestrator (the displacement signal)
7. Headcount growth over twelve months
8. Hiring for data roles, yes or no
9. Tier and a fit score, computed from the above

The trigger that matters most is **displacement.** A company running self-hosted Airflow or migrating off legacy is a buyer now, not someday. That is the orchestration gap you flagged, and it is detectable.

---

## Question 2

> **Code and documentation about how you decide to gather the data.**

I built a system, not a spreadsheet. It runs on Apollo through a Google Apps Script, in clean modules: config, schema, an Apollo client, the scoring logic, the sheet service, and the two pipeline stages.

The flow:

```mermaid
flowchart LR
    A[Search Apollo] --> B[Enrich each company]
    B --> C[Score and tier]
    C --> D[Route to US or UK sheet]
```

1. **Search.** Pull companies from Apollo by industry, size, geography, and a modern-stack filter (Snowflake, Databricks, or dbt), so only companies with a real data stack enter the pipeline.
2. **Enrich.** For each company, pull department headcount (the data-team size), the full technology stack, and headcount growth.
3. **Score and tier.** Score on data-team size, stack, displacement signal and growth, then assign the tier.
4. **Route.** Send every qualifying company to the US or UK sheet, sorted by tier then score, so the best accounts sit at the top.

The script is self-chaining, so it runs unattended past Google's time limits and stops itself when done. The hiring signal is filled by a second pass using Parallel, with a strict rule: it only answers yes on clear evidence, and answers unsure rather than guess.

I also built a **separate detection tool** (this repository) that reads a company's live infrastructure from public DNS and certificate data. It confirms the self-hosted-orchestrator signal that bought technology data can miss. Apollo builds the list, that tool confirms the hottest signal.

---

## Question 3

> **A CSV with a sample of 100 records with those data points. It doesn't have to be complete, but if it's incomplete, you should discuss that.**

**The full list is here: [Orchestra Target Accounts (Google Sheet)](https://docs.google.com/spreadsheets/d/18xjs1mfUL3_t7KrMAW-CXX19ZmU1NM7FJ9EQN1hfWIU/edit?usp=sharing)**

The sheet has three tabs: `raw` (every company enriched), `US`, and `UK` (the qualified accounts, sorted by tier then fit score). It is complete and fully enriched. Every company has firmographics, data-team size, full stack detection, warehouse, displacement signal, growth and the hiring signal. There are no gaps to discuss.

The result is **240 qualified companies, 167 US and 73 UK**, which is more than the 100 asked for, split across two geographies:

| | US | UK | Total |
|---|---|---|---|
| **Must-Win** | 57 | 28 | **85** |
| **Stretch** | 30 | 11 | **41** |
| **Self-Serve** | 80 | 34 | **114** |
| **Total** | **167** | **73** | **240** |

Two findings worth noting:

1. **About half of every company I pulled qualified.** The filters are well-targeted.
2. **The US produced more than twice as many qualified companies as the UK** for the same filters, because the UK market is more locked into the Microsoft stack. That is a real market-structure finding, not a gap in the data.

---

## Question 4

> **Note some additional ways you could improve the system if you had more time.**

Two layers: make the signal sharper, then activate it.

**Layer one: sharpen the signal (more accurate, higher intent)**

1. **Map the data team from LinkedIn directly**, so team size is a real headcount, not an estimate.
2. **Add live migration detection**: job posts about moving off Airflow, engineering blogs about platform rebuilds, and the DNS tool catching live self-hosted orchestrators. This catches the buying moment.
3. **Add intent data**: with Orchestra's pixel installed, see which target companies are already researching orchestration.
4. **Add change detection**: re-run on a schedule and surface companies that just added Snowflake or just started hiring data engineers. Change is intent.

**Layer two: activate it**

1. For each account, **research how their data team creates value** and build the outreach angle from that.
2. Wire the list into **SmartLead** to generate and send personalized email automatically.
3. Use **HeyReach** for LinkedIn outreach in parallel.
4. **Feed reply and closed-won data back into the score**, so the model tunes itself against what actually closes. The list becomes a system that improves with every campaign.

---

## Question 5

> **Now you have this incredible data and list, what would you do next? List the top 3 high-impact things you would do.**

1. **Find the person, not just the company.** For each Must-Win account, identify the champion (Head of Data, Data Architect, Data and Insights Manager) and enrich their verified email and mobile. The list gives you companies, this turns each into a named person you can reach.

2. **Start outreach with the 85 Must-Win accounts, with a real angle.** Lead with the tier that clears the revenue bar. For each, open with their likely pain, the Airflow burden and the orchestration gap, not a generic pitch. Prioritize the highest scores and the ones hiring data roles now.

3. **Prepare the materials.** Build the outreach assets: a one-pager for the lean-team, big-stack buyer, proof points from your closest analogous customers, and a short sequence built around displacement. So when the champion replies, there is something credible to land on.
