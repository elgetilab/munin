# The path between frontend and backend

In a split install the frontend reaches retrieval at `BACKEND_URL`. That path
must be private. Retrieval takes the user's identity from headers the gateway
sets (`X-Munin-Email` and friends), so anyone who can reach its port directly
can act as any user. `MUNIN_GATEWAY_TOKEN` (set on both halves by
`scripts/configure.sh`) makes retrieval refuse identity headers without the
gateway's token, but treat that as the second line of defence.

Three ways to build the path, in order of preference:

| | How | Frontend `BACKEND_URL` |
|---|---|---|
| Reverse SSH tunnel, container | the backend's `tunnel` compose profile | `http://127.0.0.1:18080` (the default) |
| Reverse SSH tunnel, systemd | `backend/config/munin-tunnel.service`, what the reference deployment runs | `http://127.0.0.1:18080` |
| VPN or private network | retrieval bound to the VPN address | `http://<backend VPN address>:8080` |

The tunnel needs no inbound port on the backend: the backend dials out to the
VPS and asks it to forward its `127.0.0.1:18080` back. The VPS listens on
loopback only (`GatewayPorts no`), so the forwarded port is not public either.

## On the VPS: the tunnel user

`frontend/bootstrap.sh --tunnel-pubkey "<key>"` does all of this. By hand, as
root:

```bash
adduser --system --shell /usr/sbin/nologin tunnel
usermod -d /home/tunnel tunnel
mkdir -p /home/tunnel/.ssh
echo 'restrict,port-forwarding,permitlisten="127.0.0.1:18080",command="/bin/false" ssh-ed25519 AAAA... munin-tunnel' \
    > /home/tunnel/.ssh/authorized_keys
chown -R tunnel:nogroup /home/tunnel/.ssh
chmod 700 /home/tunnel/.ssh && chmod 600 /home/tunnel/.ssh/authorized_keys
```

The options limit the key to exactly one thing: forwarding that one listen
address. No shell, no command, no other port. If sshd restricts logins with
`AllowUsers`, add `tunnel` there and restart sshd.

## On the backend: the container (any host)

`scripts/configure.sh --mode backend ... --vps-host vps.lab.example.edu` sets it
up: it adds the `tunnel` profile, generates a key at
`.runtime/config/tunnel/id_ed25519`, and prints the `authorized_keys` line
above with that key filled in. The relevant `.env` keys, if you set it up
yourself:

```bash
COMPOSE_PROFILES=rag,pipeline,monitoring,tunnel
MUNIN_VPS_HOST=vps.lab.example.edu
MUNIN_TUNNEL_KEY=/path/to/id_ed25519      # mode 600
MUNIN_TUNNEL_USER=tunnel                  # the default
```

The container runs `autossh -R 127.0.0.1:18080:retrieval:8080` and restarts on
its own. The VPS host key is pinned on the first connection (into
`.runtime/config/tunnel/known_hosts`); if you rebuild the VPS, delete that file.

## On the backend: systemd (the reference deployment)

`sudo backend/deploy.sh tunnel` installs `munin-tunnel.service`, filling in
`MUNIN_VPS_HOST` from the cluster env file, with the key at
`/root/.ssh/munin_tunnel`. It forwards to `localhost:8080`, where retrieval's
port is published. Use this when the backend host has systemd and you already
manage it with `deploy.sh`; otherwise use the container.

## A VPN or private network instead

Bind retrieval to the backend's address on that network, in the backend `.env`:

```bash
RETRIEVAL_BIND_ADDR=10.8.0.2      # the backend's VPN address
```

and point the frontend at it: `BACKEND_URL=http://10.8.0.2:8080`. Never
`0.0.0.0`, and never a network other people are on. Firewall the port to the
frontend's address if the network is shared at all.

## Checking it

On the VPS:

```bash
ss -tlnp | grep 18080                  # a listener on 127.0.0.1:18080
curl -s http://127.0.0.1:18080/health  # retrieval answers through the tunnel
```

| Symptom | Cause |
|---|---|
| `remote port forwarding failed for listen port 18080` | the port is still held by an old session (it frees itself within a minute), or the key's `permitlisten` names another address |
| `Permission denied (publickey)` | the key is not in `~tunnel/.ssh/authorized_keys`, or `tunnel` is missing from sshd's `AllowUsers` |
| `Host key verification failed` | the VPS was rebuilt: delete the pinned `known_hosts` entry |
| Health answers but every logged-in request is 401 | `MUNIN_GATEWAY_TOKEN` differs between the halves |
