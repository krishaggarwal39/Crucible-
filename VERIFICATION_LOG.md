# Verification Log

## RBAC Implementation (Phase 7)

**Goal:** Admin vs. Member privileges for Agent Configs, AI Operations, and Evaluation Baselines.

**Verification Steps (Pending Rebuild):**
- Verify Admin user can see `Agent Configs` and `AI Operations` in the sidebar.
- Verify Admin user can access `/agents` and `/operations` pages.
- Verify Admin user can see "Set as Baseline" toggle on evaluation details.
- Verify Member user does NOT see those sidebar items.
- Verify Member user gets redirected to `/` when trying to access `/agents`.
- Verify Member user gets `403 Forbidden` if they try to call `POST /api/v1/agent-configs/` or `POST /api/v1/evaluations/{id}/baseline` via API.
