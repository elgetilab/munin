# Munin: End-to-end verification

Run these checks after both [SETUP-CLUSTER.md](SETUP-CLUSTER.md) and [SETUP-VPS.md](SETUP-VPS.md) are complete. They confirm each side individually, then exercise the full path.

## Cluster-side checks

Run on the cluster head as root.

- [ ] `curl -s http://127.0.0.1:8080/api/status | jq` returns `ok: true` and shows the loaded embedding and LLM models.
- [ ] `curl -s http://127.0.0.1:8000/v1/models | jq` lists your vLLM model.
- [ ] `docker ps` shows `qdrant`, `neo4j`, `grobid`, `searxng`, `retrieval`, `sandbox` running.
- [ ] `systemctl is-active munin-tunnel deepresearch-daemon` returns `active` for both.
- [ ] `journalctl -u munin-tunnel -n 20 --no-pager` shows a successfully connected reverse tunnel.

## VPS-side checks

Run on the VPS as the admin user.

- [ ] `docker compose ps` shows `caddy`, `munin-auth`, `api-gateway`, `tusd`, `hook-service` all up.
- [ ] `ss -tlnp | grep 18080` shows a listener on port 18080. This listener is created by the cluster's autossh, so its presence confirms the tunnel is up.
- [ ] `curl -s http://127.0.0.1:18080/api/status | jq` returns `ok: true`. This proves the VPS can reach the cluster's retrieval API through the tunnel.

## End-to-end

Run from your workstation against your domain.

- [ ] HTTPS landing page loads:
  ```bash
  curl -sI https://<your-domain>/ | head -1
  ```
  Expect `HTTP/2 200`.
- [ ] Login OTP: visit `https://chat.<your-domain>`, enter a whitelisted email, receive a 6-digit code, enter it, land in the chat UI.
- [ ] Chat round-trip: send any message in the chat UI. The response should stream in token-by-token.
- [ ] Persona switching: open the persona menu in the chat UI; the personas you defined in `shared/personas/` should appear; selecting a different one should change the assistant's behaviour on the next message.
- [ ] Upload: drag a PDF into the upload UI at `https://upload.<your-domain>`. The file should appear under `/mnt/uploads/complete/<your-email>/` on the VPS within a few seconds.
- [ ] Sandbox tool: in chat, ask the assistant to "run python: print(2+2)". The task log should show a `run_python` tool call returning `4`.
- [ ] API key: in the chat settings, create an API key (you should get back an `sk-munin-...` token). Then from your workstation:
  ```bash
  curl -H "Authorization: Bearer sk-munin-..." https://api.<your-domain>/v1/models
  ```
  Expect a JSON list including your vLLM model.
- [ ] Admin Metrics dashboard: sign in as an admin user, open
  Admin → Metrics. The 8 panels should render with at least the
  "no data in this window" state (since you just deployed, most
  series will be empty -- that's fine). If you see
  `Metrics proxy not configured (KB_GATE_TOKEN unset)` or
  `auth role lookup failed: 401`, the cluster's `KB_GATE_TOKEN`
  doesn't match the VPS's. See `shared/docs/MONITORING.md`
  § Reproducibility for the sync procedure.

## If something fails

Common causes, in roughly the order they tend to happen.

- **Caddy issues TLS but the page never loads.** DNS A records do not actually point to the VPS. Re-check the records, then `docker logs caddy`.
- **Login email never arrives.** Check `docker logs munin-auth` for SMTP errors. Common causes: `SMTP_FROM` is on a domain you do not own (most providers reject this), port 25 is blocked by your VPS provider (use 587 with auth), SPF/DKIM not set up so the OTP lands in spam.
- **Chat hangs forever after sending a message.** Tunnel down. Check `systemctl status munin-tunnel` on the cluster, `ss -tlnp | grep 18080` on the VPS, and `docker logs api-gateway` on the VPS for connection-refused errors.
- **Empty retrieval results.** Qdrant has no documents indexed yet. Run the paper pipeline (`backend/scripts/pipeline/paper_pipeline.py`) to ingest papers, or upload a personal document via the upload UI to test user-doc retrieval.
- **vLLM returns 503.** Either the model is still downloading on first start, or it is out of VRAM. Check `journalctl -u vllm` and `nvidia-smi`. Smaller model or larger GPU.
- **`run_python` tool never returns.** Sandbox container not running. `docker ps | grep sandbox` on the cluster, `docker logs sandbox` if missing.
- **Deep research never starts a SLURM job.** `journalctl -u deepresearch-daemon -n 50`. Common causes: SLURM partition name in the daemon config does not match your cluster's partition, GPU resource request does not fit your nodes.
- **API key returns 401 even though it looks valid.** Check that you are hitting `https://api.<your-domain>` (the API subdomain bypasses session auth), not `https://chat.<your-domain>` (which expects a session cookie).
- **Admin → Metrics tab shows `auth role lookup failed: 401`.** The cluster's `KB_GATE_TOKEN` doesn't match the VPS's. Run the diff one-liner from `shared/docs/MONITORING.md` § Reproducibility to confirm both `first6=/last4=` outputs match. If they don't, copy the VPS-side value to `/opt/munin/docker/.env` on the cluster, then `docker compose --profile rag up -d --force-recreate retrieval`.

When all end-to-end checks pass, you have a working Munin instance. Tell your users.
