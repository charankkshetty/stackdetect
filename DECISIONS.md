# Where I Challenged the Brief, and Why

You gave a starting Ideal Customer Profile and said it could be improved. It could. Here is each decision I made, and the evidence behind it. Nothing here is a guess. Every change comes from your own customers.

The one line that ties it all together: **your ideal customer is not an industry or a company size. It is a shape.** A lean data team on a modern stack that has outgrown its orchestrator.

---

## The evidence: your own customers

I pulled the data-team size for your customers. This one table is the whole argument.

| Customer | Industry | Employees | Data team | Read |
|---|---|---|---|---|
| Workpath | Software (strategy execution) | 37 | 2 | Micro-team |
| Alignd | Healthcare (value-based care) | 49 | 1 | Micro-team |
| Trust & Will | Legaltech | 150 | 1 | Micro-team |
| Akebia | Pharma / biotech | 200 | 1 | Micro-team |
| Medik8 | Cosmetics / CPG | 350 | 0 (under-counted) | Micro-team |
| Usercentrics | Software (privacy tech) | 380 | 8 | Small-team |
| Graniterock | Construction materials | 700 | 3 | Micro-team |
| Powell Industries | Electrical mfg | 3,200 | 2 | Micro-team, big company |
| CoorsTek | Ceramics mfg | 5,000 | 3 | Micro-team, big company |
| Boels Rental | Equipment rental | 8,500 | 8 | Small-team |
| Hellmann | Logistics | 12,000 | 11 | Small-team |
| Experian | Data services | 26,000 | 602 | Outlier / showcase logo |

**What this table shows in three numbers:**

- Every customer except one runs a data team between **0 and 11 people**.
- Company size ranges from **37 to 12,000**. The data team barely moves. Powell has 3,200 employees and 2 data staff. Workpath has 37 employees and 2 data staff. Same team, 86 times the company.
- Then Experian: **26,000 people, 602 data staff**. That is the logo on the homepage, and it is the exception, not the pattern.

Industry goes from software to pharma to ceramics to logistics. Size goes from 37 to 12,000. Neither predicts anything. The one thing every customer shares, except the outlier, is a data team under a dozen people. That is the Ideal Customer Profile. Not an industry, not a size, a shape.

---

## The five decisions that follow from it

### 1. Industry is not the primary filter. Team shape is.

**Brief:** target manufacturing and similar traditional industries.

**Decision:** target the data-team shape first, industry second.

**Evidence:** the customers above span ceramics, electrical equipment, construction, logistics, energy, software, pharma. As industries they look nothing alike. As a shape they are identical. Industry did not predict who bought. Shape did.

### 2. Company size is a weak signal.

**Brief:** about 1,000 to 10,000 employees.

**Decision:** demote size to secondary context.

**Evidence:** CoorsTek at 5,000 and Workpath at 37 are both customers. A 5,000-person company can have a 3-person data team. Size alone tells you almost nothing about whether there is a buyable data team inside.

### 3. The real signal is data-science team size.

**Brief:** no clear metric given.

**Decision:** make data-science team size the primary quantifier.

**Evidence:** the 0-to-11 band across every customer except Experian. That tight band is the clearest signal in the whole exercise.

**The bands I built from it:**

| Band | Data team size | Verdict |
|---|---|---|
| Too small | 0 to 2 | Nobody to sell to, or fully outsourced. Excluded. |
| Must-Win | 8 to 20 | Big enough to feel pain, too small to run infra. The bullseye. |
| Stretch | 21 to 40 | Higher value, harder to win. |
| Self-Serve | 3 to 7 | Real fit, lower value. Keep as inbound. |
| Too mature | 40+ with a platform team | Runs its own orchestrator. Excluded. |

### 4. Manufacturing stays, but sharpened.

**Brief:** manufacturing is a good fit.

**Decision:** keep manufacturing, but only manufacturers with the shape. Exclude the rest.

**Evidence:** Powell and CoorsTek prove manufacturers fit. But a manufacturer locked into SAP, SSIS, or Microsoft Fabric has no modern pipeline to orchestrate and will never convert. The exclusions matter as much as the includes.

### 5. Split the list by revenue, not just fit.

**Brief:** revenue is not a strong indicator.

**Decision:** agree it is not a filter, but split the qualified list by deal size.

**Evidence:** your current customers prove fit, but many sit below a healthy deal size. Fit and revenue are not the same thing. So I lead outreach with the 85 Must-Win accounts, roughly 1.7 to 3.4 million in pipeline, instead of spreading effort evenly.

---

## What I kept, because the evidence backed it

- **Snowflake first, Databricks second.** Snowflake dominates across your customers and every competitor. Databricks is a proven second (CoorsTek, Octopus). Kept exactly as you had it.
- **The migration and displacement trigger.** You said migrations create an orchestration gap and that this is the best trigger. The evidence agreed completely. Across every competitor, the repeating pattern is people migrating off self-hosted Airflow. That is the strongest buying signal there is, and it is the one thing you said you could not track. I built the detection for exactly that.

---

## The through-line

I did not reject your instincts. I sharpened them with evidence.

You were right that manufacturing fits, that migrations are the trigger, and that Snowflake users are good targets. What the evidence added was precision: it is the shape of the data team that predicts a deal, and the buying moment is detectable from the displacement signal you flagged as untrackable.

Size and industry tell you who to look at. The data team and its infrastructure tell you who to call.
