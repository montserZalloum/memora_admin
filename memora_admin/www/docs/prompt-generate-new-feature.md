I want you to act as the documentation architect for the Memora Knowledge Portal.

Scope restriction:
Analyze only the Memora app inside Frappe.
Do not include the separate frontend project.
Do not expand to other features unless they are directly required to understand this feature.

Target feature:
CONTENT ARCHITECTURE

Goal:
Analyze this feature inside the Memora Frappe app and prepare a code-grounded documentation plan for it using our portal pattern.

Core rules:
1. Markdown is the single source of truth.
2. HTML is derived from markdown, not an independent source.
3. Do not invent business logic, technical behavior, or missing flows.
4. If something is unclear, mark it explicitly as:
   - Needs verification
   - TODO
   - Placeholder
5. Arabic-first content.
6. The target audience is mainly admin / operator / internal team.
7. The final page is not traditional documentation; it should be a visual feature walkthrough / product story / operation page.
8. Reuse the shared portal structure and shared components whenever possible.
9. Prefer maintainable structure over one-off page design.

What I want you to do:

Phase 1 — Analyze the feature from real code
Inspect the Memora Frappe app and identify everything directly related to this feature:
- related DocTypes
- child tables
- whitelisted methods
- backend business logic
- hooks
- scheduler / cron / background jobs
- permissions / role-dependent behavior
- reports
- portal/admin pages if present
- status transitions
- related concepts required to explain the feature

Do not guess.
Do not broaden the scope unnecessarily.
Only include closely related supporting concepts when needed.

Phase 2 — Build a feature map
Produce a structured analysis of this feature with:

- feature_name
- category
- short_description
- primary_actor
- why_it_exists
- entry_points
- related_files
- related_doctypes
- related_whitelisted_methods
- related_reports
- related_hooks_or_scheduler_jobs
- states_or_key_rules
- dependencies
- confidence
- notes
- missing_or_unclear_parts

Phase 3 — Choose the best page type
Recommend the best page type for this feature:
- Feature Walkthrough
- Admin Operation Page
- Troubleshooting Page
- Concept Page

Explain the reason briefly.

Phase 4 — Prepare the documentation plan
Prepare a portal page blueprint for this feature.

Include:

A) Metadata / frontmatter plan
- id
- title
- slug
- audience
- owner
- status
- last_updated
- last_verified_commit
- related_code
- related_doctypes
- related_endpoints_or_methods
- tags

B) Recommended page structure
Prefer this structure unless the feature clearly needs variation:

1. Hero
2. Quick Summary
3. Visual Flow
4. Admin Journey / Operator Journey
5. System Behavior
6. Common Cases / Edge Cases
7. Troubleshooting
8. Technical Details
9. Related Pages

C) Visual ideas
Suggest the best visual treatment for this feature:
- flow diagram
- decision tree
- timeline
- state machine
- checklist
- comparison cards
- access matrix
- troubleshooting table
- FAQ
- screenshots block
- status ladder

D) Evidence
List the exact files and code areas that should be used as source material.
Be concrete.
If uncertain, mark Needs verification.

Phase 5 — Prepare writing guidance for the actual page
Before generating the real markdown page, provide:
- what should definitely be explained
- what should stay concise
- what technical details should be collapsible
- what should be shown visually instead of explained in long text
- what open questions must be resolved first

Output:
Return the result as a serious, code-grounded feature analysis and page plan.
Do not generate the final HTML page yet unless explicitly asked.
Do not generate shallow summaries.
Surface conflicts or ambiguities clearly instead of smoothing them over.

GENERATED FEATURE MOST BE:
- www/docs/backend/features/FEATURE.md
- www/docs/backend/features/FEATURE.html

Be strict about scope. Stay focused on this feature and only pull in directly necessary related concepts.