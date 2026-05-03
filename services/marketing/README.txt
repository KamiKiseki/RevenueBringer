IMPORTANT — Do not set "services/marketing" on your MAIN API service.
That breaks the build (Railpack looks for a subfolder on the wrong service).
Main API: Root Directory MUST be empty (repo root). See RAILWAY_BUILD.txt at repo root.

---

Optional SECOND service (marketing / public site) — this folder only:

1) Project → New → Empty service (or deploy repo again).
2) Connect the SAME repo.
3) On THIS new service only → Root Directory:  services/marketing
4) Deploy. startCommand: python run.py (services/marketing/railway.toml)
5) Attach autoyieldsystems.com here only after the old site is removed elsewhere.

hvac-engine (API) stays at repo root with no Root Directory override.
