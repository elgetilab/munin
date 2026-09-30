"""
Drop to an unprivileged uid, cap resources, then exec a command.

Run as root, as the last hop before user code:

    unshare --net -- python /app/app/jail.py --uid 20003 [limits] -- <cmd...>

`unshare --net` gives the process a network namespace with only a (down)
loopback, so it cannot reach the sandbox API or any container on
sandbox-net. This script then sets hard rlimits, sets no_new_privs so no
later execve can gain privilege, and switches every uid/gid to `--uid`
(which clears all capabilities), before exec'ing the command in place, so
the pid jupyter_client tracks is the kernel's.

Standalone on purpose: it runs before any app code is importable, and it
must not import anything user-writable.
"""

import argparse
import ctypes
import os
import resource
import sys

_PR_SET_NO_NEW_PRIVS = 38


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--uid", type=int, required=True)
    p.add_argument("--rlimit-as", type=int, required=True)
    p.add_argument("--rlimit-fsize", type=int, required=True)
    p.add_argument("--rlimit-nofile", type=int, required=True)
    p.add_argument("--rlimit-nproc", type=int, required=True)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if args.uid <= 0 or not cmd:
        sys.exit("jail: need a non-root --uid and a command")

    for which, value in ((resource.RLIMIT_AS, args.rlimit_as),
                         (resource.RLIMIT_FSIZE, args.rlimit_fsize),
                         (resource.RLIMIT_NOFILE, args.rlimit_nofile),
                         (resource.RLIMIT_NPROC, args.rlimit_nproc),
                         (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(which, (value, value))

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        sys.exit(f"jail: prctl(NO_NEW_PRIVS) failed: errno {ctypes.get_errno()}")

    os.setgroups([])
    os.setresgid(args.uid, args.uid, args.uid)
    os.setresuid(args.uid, args.uid, args.uid)
    # Fail closed if the switch did not stick.
    if os.getuid() != args.uid or os.geteuid() != args.uid:
        sys.exit("jail: uid switch failed")
    try:
        os.setuid(0)
    except PermissionError:
        pass
    else:
        sys.exit("jail: could regain root")

    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main(sys.argv[1:])
