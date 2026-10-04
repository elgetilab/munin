# SETUP-CLUSTER.md: replaced

Retired 2026-10 for the public release. Installing is now one guide,
[INSTALL.md](INSTALL.md), driven by `scripts/configure.sh` for any of three
modes (one machine, one server, or a split backend and frontend). The cluster
and `deploy.sh` specifics this file used to carry, corrected, are in
[docs/install/reference-deployment.md](docs/install/reference-deployment.md),
and the tunnel in [docs/install/tunnel.md](docs/install/tunnel.md).

The original is kept at [docs/archive/SETUP-CLUSTER.md](docs/archive/SETUP-CLUSTER.md). Several of
its steps no longer match the code (the model is set with `deploy.sh model
activate`, the domain with `MUNIN_DOMAIN`, Deep Research needs no daemon), so
do not follow it.
