---
name: AGENT CRASH LAB
description: Every crash gets an investigation. Reliability testing for tool-using AI agents, set as an air-accident investigation docket.
colors:
  report-paper: "#f1f4f6"
  fresh-sheet: "#fbfcfd"
  desk-grey: "#e6ebef"
  rule: "#c3ccd4"
  rule-soft: "#dbe1e6"
  rule-strong: "#8d9aa6"
  docket-navy: "#0d1b2a"
  navy-2: "#33475b"
  slate: "#56677a"
  finding-red: "#c8102e"
  finding-red-ink: "#b00e28"
  fail-ink: "#a50d25"
  pass-green: "#1d6f4b"
  pass-ink: "#17603f"
  warn-amber: "#9a5b00"
  focus-blue: "#1f5f8b"
  series-v1: "#8a3b12"
  series-v2: "#1f5f8b"
  exhibit-ink: "#d6dee6"
  evening-paper: "#0b1622"
  evening-sheet: "#0f1d2c"
  evening-raised: "#162839"
  evening-rule: "#2a3e52"
  evening-rule-soft: "#1d3043"
  evening-rule-strong: "#4b627a"
  evening-ink: "#e8eef3"
  evening-ink-2: "#b4c2cf"
  evening-slate: "#8597a9"
  evening-finding-red: "#ff4d5e"
  evening-finding-red-ink: "#ff7a86"
  evening-fail-ink: "#ff9aa3"
  evening-pass: "#4cc38a"
  evening-pass-ink: "#8fe0b5"
  evening-warn: "#f0a940"
  evening-focus: "#6fb5e6"
  evening-series-v1: "#f08a4b"
  evening-series-v2: "#6fb5e6"
  evening-exhibit: "#07111b"
  white: "#ffffff"
typography:
  display:
    fontFamily: "Archivo, 'Arial Narrow', ui-sans-serif, sans-serif"
    fontSize: "clamp(40px, 5.4vw, 74px)"
    fontWeight: 800
    lineHeight: 0.96
    letterSpacing: "-0.02em"
    fontVariation: "'wdth' 72"
  headline:
    fontFamily: "Archivo, 'Arial Narrow', ui-sans-serif, sans-serif"
    fontSize: "clamp(28px, 3.6vw, 46px)"
    fontWeight: 800
    lineHeight: 1.02
    letterSpacing: "-0.015em"
    fontVariation: "'wdth' 76"
  title:
    fontFamily: "Archivo, 'Arial Narrow', ui-sans-serif, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.005em"
    fontVariation: "'wdth' 88"
  figure:
    fontFamily: "Archivo, 'Arial Narrow', ui-sans-serif, sans-serif"
    fontSize: "30px"
    fontWeight: 800
    lineHeight: 1
    fontVariation: "'wdth' 78"
  stamp:
    fontFamily: "Archivo, 'Arial Narrow', ui-sans-serif, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "0.04em"
    fontVariation: "'wdth' 75"
  body:
    fontFamily: "'Public Sans', ui-sans-serif, system-ui, 'Segoe UI', sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.6
    fontFeature: "'tnum' 1"
  body-dense:
    fontFamily: "'Public Sans', ui-sans-serif, system-ui, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "'tnum' 1"
  label:
    fontFamily: "'Public Sans', ui-sans-serif, system-ui, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1
    letterSpacing: "0.08em"
  data:
    fontFamily: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12.5px"
    fontWeight: 500
    lineHeight: 1.6
rounded:
  none: "0px"
  sm: "2px"
  md: "3px"
  dot: "50%"
spacing:
  xs: "6px"
  sm: "10px"
  md: "16px"
  lg: "22px"
  xl: "36px"
  section: "clamp(56px, 8vw, 104px)"
  gutter: "clamp(16px, 4vw, 48px)"
components:
  button-primary:
    backgroundColor: "{colors.docket-navy}"
    textColor: "{colors.report-paper}"
    typography: "{typography.body-dense}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  button-primary-lg:
    backgroundColor: "{colors.docket-navy}"
    textColor: "{colors.report-paper}"
    rounded: "{rounded.sm}"
    padding: "0 22px"
    height: "46px"
  button-secondary:
    backgroundColor: "{colors.desk-grey}"
    textColor: "{colors.docket-navy}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  button-danger:
    backgroundColor: "{colors.finding-red}"
    textColor: "{colors.white}"
    rounded: "{rounded.sm}"
    padding: "0 14px"
    height: "34px"
  input-text:
    backgroundColor: "{colors.fresh-sheet}"
    textColor: "{colors.docket-navy}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "11px 12px"
  docket-strip:
    textColor: "{colors.slate}"
    typography: "{typography.label}"
    padding: "10px 16px 10px 0"
  probable-cause:
    backgroundColor: "{colors.fresh-sheet}"
    textColor: "{colors.docket-navy}"
    rounded: "{rounded.none}"
    padding: "16px 18px 18px"
  finding-stamp:
    textColor: "{colors.finding-red}"
    typography: "{typography.stamp}"
    rounded: "{rounded.none}"
    padding: "6px 12px 7px"
  event-rail:
    backgroundColor: "{colors.fresh-sheet}"
    textColor: "{colors.docket-navy}"
    rounded: "{rounded.none}"
    padding: "9px 18px"
  exhibit-tag:
    backgroundColor: "{colors.docket-navy}"
    textColor: "{colors.evening-ink-2}"
    rounded: "{rounded.none}"
    padding: "10px 14px"
  case-cover:
    backgroundColor: "{colors.docket-navy}"
    textColor: "{colors.evening-ink}"
    rounded: "{rounded.none}"
    padding: "clamp(24px, 4vw, 44px)"
  case-cover-evening:
    backgroundColor: "{colors.evening-ink}"
    textColor: "{colors.docket-navy}"
  panel:
    backgroundColor: "{colors.fresh-sheet}"
    rounded: "{rounded.md}"
    padding: "18px 20px"
---

# Design System: AGENT CRASH LAB

## Overview

**Creative North Star: "The Investigation Docket"**

Every crash gets an investigation. The system is set as an air-accident investigation report: cool report paper, docket-navy ink, ruled tables, a sequence-of-events rail with T+ times, and a red finding stamp pressed onto the record. Pages read as documents in a case file, not as marketing panels. Hairline rules and full-width ink rules divide the record where another product would reach for cards.

Two scenes share one set of roles. Report paper (light) is the default, for desks by day and projectors in lit rooms. The evening docket (dark) re-steps the same roles to navy paper with paper ink, each pair re-checked for contrast. The density is a report's: generous section breaks, tight internal rows, numbers aligned in tabular figures.

Red has one job. It marks what went wrong: faults, failures, probable cause, the finding stamp. Everything else, including every action, is written in ink.

**Key Characteristics:**
- Report paper and docket-navy ink; finding red only for faults, failures, probable cause and stamps.
- Condensed Archivo headings (72–88% width), Public Sans text, JetBrains Mono only for IDs, tool names, T+ times and measured values.
- Hairline rules and 2px ink top-rules instead of cards; near-square corners (2–3px).
- One rotated element on any page: the finding stamp.
- Motion as record-keeping: entries are written in left to right, stamps press in, everything else is feedback under 200 ms.

## Colors

A two-ink document palette (paper and navy) with one law-bound red and a small set of verdict colors.

### Primary
- **Docket Navy** (docket-navy): the ink. Body text, headings, primary buttons, the 2px top-rule that opens every ledger, procedure and fee table, the closing band, the auth case-cover and the fixed agent's bar. In the evening docket it becomes Evening Ink (evening-ink).
- **Finding Red** (finding-red; evening-finding-red): faults, failures, probable cause, stamps, the v1 hazard hatch, failing cells and dots, invalid-field borders. Text in red uses Finding Red Ink (finding-red-ink / evening-finding-red-ink) or Fail Ink for legibility at small sizes.

### Secondary
- **Pass Green** (pass-green; evening-pass): passing episodes, pass status, "Closed" in the docket strip (as pass-ink). Always paired with an icon or text, never colour alone.
- **Warn Amber** (warn-amber; evening-warn): high severity and flaky status in the dashboard.
- **Focus Blue** (focus-blue; evening-focus): focus rings, text selection, search-match highlight. Not an accent.

### Tertiary
- **Series v1 Rust / Series v2 Docket Blue** (series-v1, series-v2 and their evening steps): dashboard chart series and agent names in the dashboard verdict only. The public site does not use them; there the failing v1 is drawn in the red hazard hatch and the fixed v2 in ink.

### Neutral
- **Report Paper** (report-paper; evening-paper): page background.
- **Fresh Sheet** (fresh-sheet; evening-sheet): a page laid on the desk: the event rail, panels, inputs, tables, dialogs.
- **Desk Grey** (desk-grey; evening-raised): secondary buttons, hovered rows, chips, notices.
- **Rules** (rule, rule-soft, rule-strong and evening steps): hairlines between rows, section dividers, input borders (rule-strong).
- **Navy 2** (navy-2; evening-ink-2): secondary text, ledes, table body copy.
- **Slate** (slate; evening-slate): docket metadata, captions, T+ times, column heads.
- **Exhibit Navy** (docket-navy light, evening-exhibit dark, text exhibit-ink): raw-data exhibits (code blocks, the video frame and its Exhibit tag) stay on navy in both themes.

### Named Rules
**The Red Is Law Rule.** Finding red appears only on faults, failures, probable cause and stamps. Never on a button, link, heading, brand mark or decoration. If nothing failed, nothing on the screen is red.

**The Ink Acts Rule.** Every action is written in ink: primary buttons are navy on paper (paper on navy in the evening), never coloured.

**The Hatch Means Fault Rule.** The red diagonal hatch (2px red, 4px gap, -45deg) marks injected faults and the failing agent's measurement. It is not a texture.

## Typography

**Display Font:** Archivo, variable width (with Arial Narrow fallback), loaded from Google Fonts at wdth 62–125, wght 400–800.
**Body Font:** Public Sans (with system-ui fallback), wght 400–700.
**Label/Mono Font:** JetBrains Mono (with ui-monospace fallback), wght 400 and 600.

**Character:** Condensed, heavy Archivo reads as a report's case headings, pressed tight; Public Sans is the plain government-form voice of the record. Mono is evidence, not style.

### Hierarchy
- **Display** (800, clamp 40–74px, 0.96, 72% width): the landing H1 only. Auth H1s run 34–52px at 74%, prose H1s 34–56px at 75%.
- **Headline** (800, clamp 28–46px, 1.02, 76% width): section headings on public pages; the closing band runs larger (32–60px at 72%).
- **Title** (700, 18px, 1.3, 88% width): dashboard H2s and dialog titles; H3 15px. Dashboard H1 is 32px at 80%.
- **Figure** (800, 22–34px, 1.0, 75–80% width): prices, register counts, procedure verbs, plan names.
- **Body** (400, 15px, 1.6) on public pages; 14px/1.5 in the dashboard. Ledes 15.5–18px in navy-2, capped at 58–68ch. Tabular figures on throughout.
- **Label** (600, 12px, 0.08em tracking, uppercase): the docket strip's record fields only. The Probable Cause heading uses the same caps form as its own block heading.
- **Data** (JetBrains Mono, 12–14px): IDs, tool names, T+ times and measured values.

### Named Rules
**The Mono Is Evidence Rule.** Monospace sets only IDs (ACL-ECOM-0002, ORD-1001), tool names (issue_refund), T+ times and measured values (19 / 100). Never headings, labels, buttons or decoration.

**The No Eyebrow Rule.** No tag, kicker or small caps line sits above a heading. The docket strip is a record of real fields (docket number, report type, agent, status), not a label for the heading under it.

**The Condensed Heading Rule.** Archivo headings run between 72% and 88% width; the wordmark is the only Archivo set wide (110%).

## Layout

A document column, not a card grid. Public pages sit in a 1240px container with a fluid gutter (16–48px). The landing first viewport splits 7:5: docket strip across the top, then headline, probable cause and actions on the left, the event rail on the right; below 900px it stacks to one column with the rail capped at 620px.

Sections open with a 1px rule and a large break (56–104px). Ledger tables, procedures, FAQs and fee tables open with a 2px or 1px ink rule and divide rows with hairlines. Prose caps at 720px, forms at 560px. The procedure is a four-column ruled list (number, verb, description, evidence) that collapses to three at 1080px and one at 760px.

The dashboard is a 232px sticky sidebar plus a content page capped at 1320px with 28–32px padding; the sidebar becomes an off-canvas drawer at 760px. The trace explorer runs three columns (270 / fluid / 380) down to one.

Breakpoints in use: 1280, 1080, 960 (site nav collapses), 900, 860, 760 (mobile). Everything works at 320px.

## Elevation & Depth

Flat by default; depth comes from paper laid on paper (fresh sheet over report paper) and from rules. Shadows are long, soft and navy-tinted, used only where an object physically sits above the record: the event rail, the video exhibit, dialogs, the consent sheet, floating buttons and tooltips.

### Shadow Vocabulary
- **Sheet on desk** (`box-shadow: 0 1px 0 var(--line-soft), 0 18px 40px -24px var(--shadow)`): the event rail.
- **Exhibit** (`box-shadow: 0 24px 60px -32px var(--shadow)`): the video frame.
- **Dialog** (`box-shadow: 0 24px 60px var(--shadow)`): modal and search dialogs.
- **Floating** (`box-shadow: 0 6px 20px var(--shadow)`): back-to-top and contact buttons.

The shadow colour is navy at 16% on report paper and black at 45% in the evening docket.

### Named Rules
**The Rules Not Cards Rule.** Group content with hairlines and ink top-rules. A boxed, shadowed card is reserved for an object that sits on the record.

## Shapes

Near-square. Buttons, inputs, chips and code blocks use 2px corners; panels, dialogs and floating buttons 3px. Docket surfaces (strip, probable cause, rail, stamp, case-cover, closing band, fee table) are square. Circles appear only as rail nodes, severity dots, matrix dots and the brand target. The finding stamp is the one rotated element (-6deg), with a 2.5px red outline.

## Components

### Buttons
Sharp, typeset, written in ink.
- **Shape:** 2px corners (rounded.sm), 1px border, 600 weight.
- **Primary:** navy fill, paper text, 34px tall (46px large, 44px block, 30px small).
- **Secondary:** desk-grey fill, rule border, ink text.
- **Hover / Focus:** border steps to rule-strong; primary mixes 14% focus blue into navy. Active presses down 1px. Focus is a 2px focus-blue outline offset 2px. Transitions 100–150 ms.
- **Danger:** finding-red fill for destructive confirmation in dialogs; danger-ghost is transparent with red-ink text and a red-tinted border.

### Chips
- **Category chip:** desk-grey fill, navy-2 text, 12px, 2px corners.
- **Role pill:** uppercase 11px, 0.1em tracking, 1px rule-strong border; the admin pill takes an ink border.

### Cards / Containers
- **Corner Style:** 3px (panel) or square (docket surfaces).
- **Background:** fresh sheet on report paper.
- **Shadow Strategy:** none at rest; see Elevation & Depth.
- **Border:** 1px rule-soft.
- **Internal Padding:** 18px 20px (16px 14px on mobile).

### Inputs / Fields
- **Style:** fresh-sheet fill, 1px rule-strong border, 2px corners, 11px 12px padding, 15px text.
- **Focus:** border turns focus blue with a 3px focus ring at 25%.
- **Error:** red border, inline error in fail-ink beneath, error summary in a red-tinted box at the top.

### Navigation
- **Site header:** sticky, translucent report paper with blur, 1px rule below. Links in navy-2 at 500; hover and current page draw a 1px ink underline from the left (250 ms). Collapses to a full-width ruled drawer at 960px.
- **Dashboard sidebar:** 232px, 6px 8px links in navy-2; current page on desk grey at 700.
- **Footer:** opens with a 2px ink rule, slate text.

### Docket Strip (signature)
A record header: a 2px ink rule above, a hairline below, fields separated by vertical hairlines. Labels in 12px tracked caps slate; values (docket number, agent) in mono ink; the status field in pass ink. Every field carries a real value.

### Event Rail (signature)
The sequence of events. A fresh sheet with a figcaption ("Sequence of events"), a vertical hairline spine, hollow navy nodes, T+ times right-aligned in mono slate, event text in 14px ink, tool names in mono slate. Fault rows fill the node red and turn time and tool red-ink. Playback writes each row in with a left-to-right clip reveal (600 ms, 420 ms stagger, cubic-bezier(0.16, 1, 0.3, 1)); fault rows flash a 22% red wash; the stamp presses in last. The rail is replayable and scrubbable; reduced motion shows the final record at once.

### Finding Stamp
Red 2.5px outlined caps in condensed Archivo, rotated -6deg, with a small tracked "Finding" line above. It presses in from 1.25 scale. Multiply blend on paper, normal in the evening.

### Probable Cause
A red-tinted block (5% red over fresh sheet, 50% red border, square) with a caps "Probable cause" heading in red ink and the finding in 15px ink.

### Exhibit Frame
Raw evidence on navy in both themes. The video frame's figcaption is the Exhibit tag ("Exhibit A · recorded from the real app"): 12.5px navy-2-on-navy text above the video, divided by a navy hairline.

### Measurement Bars
Horizontal bars, 22px tall, value in mono. The failing v1 is drawn in the red hazard hatch with a red inset outline and red-ink value; the fixed v2 in solid ink. Bars grow from the left (900 ms) once.

### Case Cover
The auth pages' left panel inverts the page: navy with paper ink on report paper, paper with navy ink in the evening docket. It carries the brand target, a condensed Archivo quote and a ruled list of findings with mono values.

## Do's and Don'ts

### Do:
- **Do** reserve finding red for faults, failures, probable cause and stamps; draw the failing measurement in the red hazard hatch and the fixed one in ink.
- **Do** set IDs, tool names, T+ times and measured values in JetBrains Mono, and nothing else.
- **Do** divide the record with hairlines and open ledgers with an ink top-rule (2px for record headers and tables, 1px for lists).
- **Do** keep Archivo headings condensed (72–88% width) and heavy (700–800).
- **Do** keep raw-data exhibits (code, video) on navy in both themes.
- **Do** invert the auth case-cover per theme.
- **Do** show the final state at once under reduced motion.

### Don't:
- **Don't** use red for buttons, links, emphasis, the brand mark or decoration.
- **Don't** put an eyebrow, kicker or section tag above a heading.
- **Don't** set labels, headings or step numbers in mono.
- **Don't** rotate anything other than the finding stamp.
- **Don't** use the dashboard series colours (series-v1, series-v2) on the public site.
- **Don't** wrap sections in shadowed cards; a shadow means an object sits on the record.
- **Don't** round corners beyond 3px, outside the pill-shaped mode indicator and circular nodes.
