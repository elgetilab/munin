#!/usr/bin/env bash
# ==============================================================================
# fetch_lite_state.sh - pull a Lite backend's user state, read-only, for
# merge_state.py.
# ==============================================================================
# Produces, in OUT_DIR:
#   chats.snapshot.db        backup-API snapshot of the remote chats.db, taken
#                            inside the remote retrieval container and streamed
#                            back over ssh (no temp file on the remote host)
#   user_docs/               rsync copy of the remote user_docs tree
#   user_docs.points.jsonl   Qdrant `user_docs` export (vectors + payloads),
#                            scrolled from inside the remote retrieval container
#   agent_traces/            only with --with-traces (diagnostics, optional)
#   uploads-complete.txt     names of KB uploads still queued on the frontend
#   manifest.json            counts and sha256 of what was fetched
#
# Nothing is written, restarted or deleted on the remote host. merge_state.py is
# piped to the remote python on stdin, so not even the script lands there.
#
# Usage:
#   backend/scripts/maintenance/fetch_lite_state.sh \
#       --host <admin-user>@<vps-host> --key <ssh-key> --out /path/to/stage
#
# Options:
#   --host USER@HOST        ssh target (required). One login, reused through a
#                           control socket: hosts with fail2ban see one auth.
#   --key PATH              ssh identity file
#   --out DIR               local output directory (required, created 0700)
#   --remote-data DIR       Lite data dir on the host
#                           (default: ~/munin-lite/.runtime/data)
#   --container NAME        Lite retrieval container (default: munin-retrieval)
#   --uploads-dir DIR       frontend upload queue (default: /mnt/uploads/complete)
#   --since ISO             only export Qdrant points uploaded at/after ISO
#   --with-traces           also copy agent_traces/
# ==============================================================================
set -euo pipefail

HOST="" KEY="" OUT="" SINCE="" TRACES=0
REMOTE_DATA='~/munin-lite/.runtime/data'
CONTAINER=munin-retrieval
UPLOADS=/mnt/uploads/complete
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/merge_state.py"

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST=$2; shift 2 ;;
        --key) KEY=$2; shift 2 ;;
        --out) OUT=$2; shift 2 ;;
        --remote-data) REMOTE_DATA=$2; shift 2 ;;
        --container) CONTAINER=$2; shift 2 ;;
        --uploads-dir) UPLOADS=$2; shift 2 ;;
        --since) SINCE=$2; shift 2 ;;
        --with-traces) TRACES=1; shift ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 64 ;;
    esac
done
[ -n "$HOST" ] && [ -n "$OUT" ] || { echo "--host and --out are required" >&2; exit 64; }
[ -f "$TOOL" ] || { echo "merge_state.py not found beside this script" >&2; exit 1; }

umask 077
mkdir -p "$OUT"
chmod 700 "$OUT"
CTL="$(mktemp -u "${TMPDIR:-/tmp}/fetch-lite-ssh.XXXXXX")"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20 -o ControlMaster=auto
          -o "ControlPath=$CTL" -o ControlPersist=120)
[ -n "$KEY" ] && SSH_OPTS+=(-i "$KEY")
cleanup() { ssh "${SSH_OPTS[@]}" -O exit "$HOST" 2>/dev/null || true; }
trap cleanup EXIT
rsh() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }

echo "[1/5] chats.db snapshot (backup API, inside $CONTAINER)"
rsh "docker exec -i $CONTAINER python3 - snapshot /data/chats.db -" < "$TOOL" \
    > "$OUT/chats.snapshot.db.part"
python3 - "$OUT/chats.snapshot.db.part" <<'EOF'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
ok = c.execute("PRAGMA integrity_check").fetchone()[0]
c.execute("PRAGMA journal_mode=DELETE")
c.close()
if ok != "ok":
    sys.exit(f"snapshot failed integrity_check: {ok}")
EOF
mv "$OUT/chats.snapshot.db.part" "$OUT/chats.snapshot.db"

echo "[2/5] Qdrant user_docs export (inside $CONTAINER)"
SINCE_ARG=""
[ -n "$SINCE" ] && SINCE_ARG="--since $SINCE"
rsh "docker exec -i $CONTAINER python3 - qdrant-export --collection user_docs $SINCE_ARG --out -" \
    < "$TOOL" > "$OUT/user_docs.points.jsonl.part"
mv "$OUT/user_docs.points.jsonl.part" "$OUT/user_docs.points.jsonl"

echo "[3/5] user_docs files (rsync, read-only on the remote side)"
mkdir -p "$OUT/user_docs"
rsync -a --chmod=D700,F600 -e "ssh ${SSH_OPTS[*]}" \
    "$HOST:$REMOTE_DATA/user_docs/" "$OUT/user_docs/"

if [ "$TRACES" = 1 ]; then
    echo "[3b] agent_traces"
    mkdir -p "$OUT/agent_traces"
    rsync -a --chmod=D700,F600 -e "ssh ${SSH_OPTS[*]}" \
        "$HOST:$REMOTE_DATA/agent_traces/" "$OUT/agent_traces/"
fi

echo "[4/5] queued KB uploads (counted, not copied: the frontend retries them)"
# Date and size only: the queue's directories are named by email address.
rsh "find $UPLOADS -type f -mtime -30 -printf '%TY-%Tm-%Td %s bytes\n' 2>/dev/null | sort" \
    > "$OUT/uploads-complete.txt" || true
echo "    files queued in the last 30 days: $(wc -l < "$OUT/uploads-complete.txt")"

echo "[5/5] manifest"
python3 - "$OUT" <<'EOF'
import hashlib, json, os, sqlite3, sys
out = sys.argv[1]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
db = os.path.join(out, "chats.snapshot.db")
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
tables = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
    "AND name NOT LIKE 'messages_fts_%' ORDER BY name")]
counts = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
pts = os.path.join(out, "user_docs.points.jsonl")
with open(pts) as f:
    meta = json.loads(f.readline()).get("_meta", {})
    npts = sum(1 for line in f if line.strip())
files = [os.path.join(r, n) for r, _, ns in os.walk(os.path.join(out, "user_docs")) for n in ns]
m = {"chats_snapshot": {"sha256": sha(db), "bytes": os.path.getsize(db), "rows": counts},
     "qdrant_export": {"sha256": sha(pts), "points": npts, "vectors": meta.get("vectors"),
                       "remote_points_count": meta.get("points_count")},
     "user_docs": {"files": len(files), "bytes": sum(os.path.getsize(p) for p in files)}}
with open(os.path.join(out, "manifest.json"), "w") as f:
    json.dump(m, f, indent=2)
print(json.dumps(m, indent=2))
EOF
