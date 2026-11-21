---
version: 1
slug: "server-site-py"
primary_target: "server/site.py"
related_targets: ["server/auth.py","web/site.css","web/styles.css"]
---

# Public site: landing, pricing, sign-in

Scope: `/` (landing, Persuade), `/login` `/signup` `/admin/login` `/setup` `/forgot` `/reset` `/account` `/admin` (Operate, inherit the world with restraint), `/contact` `/privacy` (Read). The dashboard `/app/` inherits the same world in Operate mode.

Audience: AI engineers and technical founders (primary); judges, professors, investors (must get it in one viewport). Action: create a free account → watch the full demo video → crash-test an agent. Proof: the seeded benchmark (19% → 99%, duplicates 15% → 0%, attacks 75% → 0%, 8 → 2 step reproduction). Pricing confirmed: Free, Pro ₹2,499, Team ₹9,999, Enterprise custom; payments not connected.

## Direction contract

THESIS: Every crash gets an investigation. The homepage is the cover page of a real investigation into a real failure (ACL-ECOM-0002, the duplicate refund), refusing the category default of a dark hero over a dashboard screenshot and a row of feature cards.

OWN-WORLD: Investigation docket. Cool report paper (#f1f4f6) and docket navy ink (#0d1b2a); evening theme inverts to navy paper with paper ink. Finding red (#c8102e) is law: only faults, failures, probable cause and stamps use it. Slate (#5b6b7a) for docket metadata. Archivo at condensed widths for report headings, Public Sans for body, JetBrains Mono only for trace data, times and IDs. Components: docket header strip (docket number, report type, status), ruled tables, exhibit tags (bordered caps labels), sequence-of-events time rail, finding stamp (red outlined caps, the only rotated element), sharp rectangular buttons, hairline rules instead of cards.

STORY: The visitor reads one real investigation, understands the product makes failures happen on purpose and proves them from the system of record, believes it because every number is reproducible, and creates a free account, watches the demo, runs a test.

FIRST VIEWPORT: Desktop 12 columns. Left 7: docket strip ("Docket ACL-ECOM-0002 · Investigation report · Closed"), H1 "Your AI agent passed the demo. Will it survive production?" at display scale, a PROBABLE CAUSE block quoting the finding, then "Create free account" (primary) and "Watch the demo" (secondary). Right 5: the sequence-of-events time rail for that failure, T+ times in mono, the timeout-after-commit fault marked red, ending in the stamped finding "DUPLICATE REFUND". Exhibit A (the demo video) starts the section below. Mobile: docket strip, H1, actions, then the rail.

FORM: Investigation docket (air-accident investigation report), position 1 on the ordered list (Impeccable's pick, chosen by the user over the roll); seed: df8d96030869b706. Signature interaction: the time rail plays back like a flight-data recorder (rows enter the record one by one, the fault row flashes red, the finding stamp presses in), and it is scrubbable and replayable. Motion grammar: entries are written into the record (clip-path reveal left to right, 500–700 ms, cubic-bezier(0.16,1,0.3,1)); stamps press (scale 1.12 → 1, slight rotation); everything else is feedback under 200 ms. Reduced motion: final state shown at once.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
