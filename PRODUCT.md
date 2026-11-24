# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
- **Primary:** AI engineers and technical founders at AI startups and development agencies (India first) who build agents that take real actions through tools: refunds, cancellations, bookings, account changes. They test before release and want evidence, not a chat transcript.
- **Also, explicitly:** judges, professors and investors evaluating the project. They need to understand what it does and see it working within minutes, without installing anything.

## Product Purpose
AGENT CRASH LAB is a testing, debugging and reliability platform for tool-using AI agents. It runs an agent inside a simulated business system, injects realistic faults while the agent works, checks the system of record (not the agent's words), and turns each failure into a minimal reproduction and a regression test. Success: a team finds a failure that would have cost real money, fixes it, and the regression test keeps it fixed.

## Positioning
The fault-to-regression loop: Stress → Detect → Explain → Minimize → Prevent → Compare. Faults are stateful (a payment gateway that commits and then times out), evaluation reads the final database state, and delta-debugging shrinks a long failing trace to the few steps that cause it. Observability tools show what happened; this makes it happen on purpose and proves the fix.

## Operating Context
- Visitor path the user confirmed: create a free account → watch the full demo video → crash-test an agent right away.
- Product surfaces: public site (landing, pricing, FAQ, contact, privacy), sign-up / sign-in / admin sign-in (email + password, Google), the dashboard (experiments, live runs, failures, trace explorer, minimizer, regression suite, integrations), an admin console, a REST API with docs, a Python SDK and CLI.
- Agents under test: built-in reference agents v1 (buggy) and v2 (fixed), Claude and Gemini tool-calling agents, or the customer's own agent via the SDK.
- Deployed on Render; runs locally with `run.bat`.

## Capabilities and Constraints
- One environment today: e-commerce (orders, payments, refunds, shipping, tickets, approvals), 30 scenarios, 8 fault types, 6 evaluators, 11-category failure taxonomy.
- Pricing confirmed by the user for display: Free; Pro ₹2,499/month; Team ₹9,999/month; Enterprise custom. Payments are not connected yet: plans must not pretend to take money.
- Not built yet (do not claim): Postgres/Redis workers, container isolation for customer code, OpenAI Agents SDK / LangGraph / CrewAI adapters, other environments, SSO, billing.
- The root-cause engine reports evidence-linked hypotheses with confidence; it never claims to know why a model decided something.

## Brand Commitments
- Name: AGENT CRASH LAB (all caps in the wordmark).
- Tagline in use: "Find failures before your customers do." Headline in use: "Your AI agent passed the demo. Will it survive production?"
- Mark: the crash-test calibration target, drawn in ink (navy on paper, paper on navy) since the docket redesign; red stays reserved for findings (`web/favicon.svg`).

## Evidence on Hand
- Real, reproducible benchmark (seeded): 300 episodes; reference agent v1 passes 23% (35/150), v2 passes 99% (149/150); duplicate side effects 10% → 0%; attack success 67% → 0%; 15 failure modes; a failing trace minimized from 8 steps to 2. `tests/test_site.py` re-runs it so published numbers cannot drift.
- Demo video of the whole product (recorded from the real app).
- No customers, testimonials, logos, press or case studies exist. Never invent them; `web/site/testimonials.json` is empty on purpose.

## Product Principles
1. Prove, don't claim: every number on the site comes from a run anyone can reproduce.
2. Check the system of record, not the agent's words.
3. One bug, one finding: fold consequences into the root failure.
4. Every failure ends as a regression test.
5. Honest scope: say what is built and what is next.

## Accessibility & Inclusion
WCAG 2.2 AA: keyboard reachable everything, visible focus, reduced-motion respected, color never the only signal (pass/fail carry icons and text), works at 320px width.
