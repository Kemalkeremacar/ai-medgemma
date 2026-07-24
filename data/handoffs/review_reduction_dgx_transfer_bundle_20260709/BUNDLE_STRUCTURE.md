# Review-Reduction DGX Bundle Structure
This document describes the curated, aggregate-only DGX transfer bundle built by `build_review_reduction_dgx_transfer_bundle.py`.
## Safety model
- The deterministic rule engine is the only live decision layer.
- MedGemma and other AI outputs are advisory shadow artifacts, not human/admin/expert approval.
- The bundle is not apply-ready and does not authorize production DB, Qdrant, or runtime writes.
- `rule_engine_results.csv`, case-level rows, and case IDs are excluded.
- The required state remains `human_admin_approval_present=false`, `apply_ready=false`, `auto_apply=false`, `runtime_decision_changed=false`, and `shadow_only=true`.
## Bundle layout
- `DGX_TRANSFER_MANIFEST.json`: file inventory, hashes, source paths, exclusions, and safety metadata.
- `DGX_TRANSFER_README.txt`: concise transfer overview and 703790 correction notice.
- `DGX_AGENT_PROMPT_COPY_PASTE.txt`: DGX-side validation and shadow-analysis instructions.
- `BUNDLE_STRUCTURE.md`: this structure and precedence document.
- `artifacts/`: curated aggregate-only analysis packages.
- `scripts/`: reproducibility builders; scripts do not authorize production execution.
- `qdrant_shadow/`: preview-only payloads, contract, and production-write warning.
## 703790 proposal
The authoritative 703790 package inside the bundle is:
`artifacts/review_reduction_703790_shadow_policy_proposal_20260720/`
Key files:
- `703790_SHADOW_POLICY_PROPOSAL.json`: disabled H40-only overlay preview and unchanged runtime state.
- `703790_shadow_policy_scenarios.json`: conservative, observation-only, and original broad counterfactual comparisons.
- `703790_prefix_decision_register.json`: governance classification by ICD prefix.
- `703790_SHADOW_MONITORING_PLAN.json`: observation window, alert thresholds, stop criteria, and promotion gates.
- `703790_SHADOW_ROLLBACK_MANIFEST.json`: shadow deactivation and unchanged-runtime verification steps.
- `703790_GOVERNANCE_REVIEW.txt`: human-readable review brief.
- `TASK_MANIFEST.json`: input, output, aggregate count, and safety metadata.
## 703790 precedence rule
The corrected 703790 package supersedes the earlier broad MedGemma prefix preview for all governance and DGX-side shadow interpretation.
- Recommended shadow cohort: `H40*` only.
- Historical result: 146 of 212 REVIEW rows matched; 7 were full-row and 139 were partial-resolution counterfactuals.
- Observation-only: `H46`, `H47.5`, `H35.3`.
- Keep manual review: `H52`, `H04`, `H18`, `H43`.
- The original 199-row/16-full-release broad result is retained only as a counterfactual.
- Actual deterministic outcome remains `REVIEW_REQUIRED`.
## Qdrant preview semantics
`qdrant_shadow/QDRANT_SHADOW_PAYLOAD_PREVIEW.jsonl` is not an upsert request. The 703790 record uses the corrected H40-only values and retains the original broad result only in `original_broad_counterfactual`.
Any separately authorized indexing must use a dedicated shadow collection, preserve all safety flags, and exclude raw historical rows and identifiers.
## Rebuild order
1. Run `scripts/build_703790_shadow_policy_proposal.py` where the original aggregate inputs are available.
2. Run `scripts/build_review_reduction_dgx_transfer_bundle.py`.
3. Validate manifest hashes, ZIP contents, safety flags, and the absence of `rule_engine_results.csv`.
4. Do not run any production apply or ingestion step from this bundle.
