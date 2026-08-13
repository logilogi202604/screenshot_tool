"""Loopback test for LAN file transfer: real sockets, no tray and no hotkey.

Discovery is deliberately not exercised here — two services in one process would
fight over the single broadcast port, and the transfer path is what can actually
lose or corrupt a file. Peers are handed in directly instead.
"""
import hashlib
import http.client
import os
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import QCoreApplication

from fileshare import (
    FileShareService,
    _safe_filename,
    _truncate_bytes,
    _unique_open,
    is_lan_ip,
    local_ip,
    parse_peer,
    proof_for,
)

app = QCoreApplication(sys.argv)

TOKEN = "0123456789abcdef"
tmp = tempfile.mkdtemp(prefix="fileshare_test_")
recv_dir = os.path.join(tmp, "inbox")
src_path = os.path.join(tmp, "payload 中文.bin")
empty_path = os.path.join(tmp, "empty.bin")

# 5 MiB of non-repeating data: big enough to span many 256KB chunks, so a
# chunking/offset bug shows up as a hash mismatch instead of passing by luck.
payload = hashlib.sha256(b"seed").digest() * 163840
with open(src_path, "wb") as f:
    f.write(payload)
open(empty_path, "wb").close()
want = hashlib.sha256(payload).hexdigest()


def make(port, token=TOKEN, name="peer", manual=()):
    cfg = {
        "fileshare_port": port,
        "fileshare_token": token,
        "fileshare_name": name,
        "fileshare_recv_dir": recv_dir,
        "fileshare_peers": list(manual),
    }
    return FileShareService(cfg, log=lambda m: print("   log:", m))


def wait_for(pred, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------- unit checks

# Path traversal must not survive, in either separator flavour: a Windows peer
# sending backslashes reaches a POSIX basename() untouched.
assert _safe_filename("../../etc/passwd") == "passwd", _safe_filename("../../etc/passwd")
assert _safe_filename(r"..\..\Windows\evil.exe") == "evil.exe"
assert _safe_filename(".bashrc") == "bashrc"
assert _safe_filename("..") == "received_file"
assert _safe_filename("/") == "received_file"
assert _safe_filename("") == "received_file"
assert _safe_filename("%E4%B8%AD%E6%96%87.txt") == "中文.txt"

# Windows-hostile names: reserved devices, forbidden characters, NTFS stream
# syntax, and the trailing dot/space that Windows silently strips.
assert _safe_filename("CON") == "_CON", _safe_filename("CON")
assert _safe_filename("con.txt") == "_con.txt"
assert _safe_filename("LPT9.log") == "_LPT9.log"
assert _safe_filename('a<b>c:d"e|f?g*h.txt') == "a_b_c_d_e_f_g_h.txt"
assert _safe_filename("report.txt:hidden") == "report.txt_hidden"
assert _safe_filename("trailing. ") == "trailing"
assert "\x00" not in _safe_filename("nul\x00byte.txt")

# Length is bounded in encoded BYTES, not characters: APFS and NTFS both cap a
# path component in bytes, so 180 CJK characters is ~540 bytes and fails to create.
long_cjk = _safe_filename("中" * 300 + ".txt")
assert len(long_cjk.encode("utf-8")) <= 180, len(long_cjk.encode("utf-8"))
assert long_cjk.endswith(".txt"), long_cjk
assert _truncate_bytes("ok.txt", 180) == "ok.txt"

# Colliding names must never overwrite.
os.makedirs(recv_dir, exist_ok=True)
fd1, p1 = _unique_open(recv_dir, "dup.txt")
fd2, p2 = _unique_open(recv_dir, "dup.txt")
os.close(fd1)
os.close(fd2)
assert p1 != p2, "second open reused the same path"
assert os.path.basename(p2) == "dup (1).txt", p2
os.unlink(p1)
os.unlink(p2)
print("UNIT OK: filename sanitising (posix + windows) + byte truncation + collisions")

# --------------------------------------------------------- addressing / peers

assert is_lan_ip("192.168.1.51") and is_lan_ip("10.1.2.3") and is_lan_ip("172.16.0.1")
assert not is_lan_ip("198.18.0.1"), "proxy fake-ip range must not count as LAN"
assert not is_lan_ip("172.32.0.1") and not is_lan_ip("8.8.8.8")

assert parse_peer("192.168.1.50", 53318)["port"] == 53318
assert parse_peer("192.168.1.50:9999", 53318)["port"] == 9999
for bad in ("", "   ", ":53318", "host:notaport", "host:0", "host:70000", None, 5):
    assert parse_peer(bad, 53318) is None, f"parse_peer accepted {bad!r}"

# This machine runs a TUN-mode proxy that answers route lookups for public
# addresses with 198.18.x.x; local_ip() must not report that to peers.
ip = local_ip()
assert not ip.startswith("198.18."), f"local_ip returned the proxy fake-ip: {ip}"
print(f"NET OK: local_ip={ip}")

manual_svc = make(45404, manual=["10.1.2.3", "10.1.2.4:6000"])
manual_hosts = {p["host"]: p["port"] for p in manual_svc.peers()}
assert manual_hosts == {"10.1.2.3": 45404, "10.1.2.4": 6000}, manual_hosts
print(f"MANUAL PEERS OK: {manual_hosts}")

# The announce path runs only inside a daemon thread, where an exception is
# swallowed and discovery just silently stops. Drive it directly so a mistake
# there fails the suite instead of turning into "the menu is empty".
tx, tx_ip = manual_svc._tx_socket()
assert tx is not None, "no announce socket"
assert tx is manual_svc._tx_socket()[0], "announce socket rebuilt on every call"
manual_svc._announce(None)          # must not raise
if tx_ip:
    assert is_lan_ip(tx_ip), f"announce socket pinned to a non-LAN address: {tx_ip}"
print(f"ANNOUNCE OK: socket pinned to {tx_ip}, limited + directed broadcast sent")

# ------------------------------------------------------------- nonce / replay

nonce_svc = make(45405)
n = nonce_svc.new_nonce()
assert n and nonce_svc.check_proof(n, proof_for(TOKEN, n)), "valid proof rejected"
assert not nonce_svc.check_proof(n, proof_for(TOKEN, n)), "nonce accepted twice (replayable)"
n2 = nonce_svc.new_nonce()
assert not nonce_svc.check_proof(n2, proof_for("wrong-token", n2)), "wrong token accepted"
assert not nonce_svc.check_proof("never-issued", proof_for(TOKEN, "never-issued")), \
    "a nonce we never issued was accepted"
assert not nonce_svc.check_proof("", ""), "empty proof accepted"
# The pending-challenge table must be bounded, or an unpaired flood eats memory.
issued = [nonce_svc.new_nonce() for _ in range(200)]
assert None in issued, "new_nonce() never refused — the nonce table is unbounded"
print("AUTH UNIT OK: single-use nonces, wrong token refused, table bounded")

# --------------------------------------------------------- real transfer test

receiver = make(45401, name="receiver")
sender = make(45402, name="sender")
assert receiver.start(), "receiver failed to bind"
assert sender.start(), "sender failed to bind"

got = []
errors = []
receiver.received.connect(lambda path, who: got.append((path, who)))
sender.failed.connect(errors.append)
receiver.failed.connect(errors.append)

peer = {"name": "receiver", "host": "127.0.0.1", "port": 45401, "id": "x"}
sender.send(src_path, peer)

assert wait_for(lambda: got or errors), "transfer neither completed nor failed in 30s"
assert not errors, f"transfer reported errors: {errors}"

path, who = got[0]
assert who == "sender", f"sender name not carried through: {who!r}"
assert os.path.basename(path) == "payload 中文.bin", f"filename mangled: {path}"
with open(path, "rb") as f:
    assert hashlib.sha256(f.read()).hexdigest() == want, "received bytes differ from source"
print(f"TRANSFER OK: {os.path.getsize(path)} bytes, sha256 matches, name={os.path.basename(path)!r}")

# A zero-byte file is a legitimate thing to send; Content-Length: 0 must not be
# mistaken for a malformed request.
got.clear()
errors.clear()
sender.send(empty_path, peer)
assert wait_for(lambda: got or errors, timeout=15.0), "empty-file transfer hung"
assert not errors, f"empty file rejected: {errors}"
assert os.path.getsize(got[0][0]) == 0, "empty file did not arrive empty"
print("EMPTY FILE OK: 0-byte transfer accepted")

# ------------------------------------------------------------- security check

got.clear()
errors.clear()
attacker = make(45403, token="wrong-token-entirely", name="attacker")
assert attacker.start(), "attacker failed to bind"
attacker.failed.connect(errors.append)
attacker.send(src_path, peer)

assert wait_for(lambda: errors, timeout=15.0), "bad token was neither accepted nor refused"
assert not got, "a file with a mismatched pairing code reached the disk"
# The refusal must be legible, not a broken pipe from being cut off mid-stream.
assert "配对码" in errors[0], f"expected a pairing-code message, got: {errors[0]!r}"
assert "pipe" not in errors[0].lower(), f"refusal leaked a socket error: {errors[0]!r}"
print(f"AUTH OK: mismatched pairing code refused before any upload ({errors[0]})")

# Queuing many files must not create a thread per file. Before this was a pool,
# selecting a thousand files in the dialog spawned a thousand threads that all
# blocked on a semaphore, and Thread.start() can raise once the OS runs out.
import threading as _threading  # noqa: E402

from fileshare import MAX_ACTIVE_SENDS  # noqa: E402

errors.clear()
dead_peer = {"name": "nowhere", "host": "127.0.0.1", "port": 45499, "id": "dead"}
before = _threading.active_count()
for _ in range(200):
    sender.send(src_path, dead_peer)
peak = max(_threading.active_count() - before for _ in range(3))
assert peak <= MAX_ACTIVE_SENDS + 1, \
    f"queuing 200 files spawned {peak} threads, expected <= {MAX_ACTIVE_SENDS}"
assert len(sender._send_workers) <= MAX_ACTIVE_SENDS, \
    f"{len(sender._send_workers)} workers for a {MAX_ACTIVE_SENDS}-slot pool"
print(f"SEND POOL OK: 200 queued files ran on <= {MAX_ACTIVE_SENDS} threads (saw {peak})")
sender._send_queue.clear()

# The token must never appear on the wire — that is the whole point of the
# challenge/response. Drive the handshake by hand and inspect what is sent.
conn = http.client.HTTPConnection("127.0.0.1", 45401, timeout=10)
conn.request("GET", "/challenge")
resp = conn.getresponse()
issued_nonce = resp.read().decode()
assert resp.status == 200 and issued_nonce, "challenge endpoint did not issue a nonce"
assert TOKEN not in issued_nonce, "the challenge leaked the token"

# A replayed proof must be refused even though it was valid once.
hdrs = {"X-Nonce": issued_nonce, "X-Auth": proof_for(TOKEN, issued_nonce),
        "Content-Length": "0"}
conn.request("POST", "/auth", body=b"", headers=hdrs)
assert conn.getresponse().status == 200, "a valid proof was refused"
conn.close()
conn = http.client.HTTPConnection("127.0.0.1", 45401, timeout=10)
conn.request("POST", "/auth", body=b"", headers=hdrs)
assert conn.getresponse().status == 403, "a replayed nonce/proof pair was accepted"
conn.close()
print("REPLAY OK: token never transmitted, captured proof cannot be reused")

# An unpaired peer must not be able to make us allocate a file at all.
conn = http.client.HTTPConnection("127.0.0.1", 45401, timeout=10)
before = set(os.listdir(recv_dir))
conn.request("POST", "/recv", body=b"x" * 1024, headers={
    "X-Nonce": "bogus", "X-Auth": "bogus", "X-Filename": "planted.txt",
    "Content-Length": "1024",
})
assert conn.getresponse().status == 403, "unauthenticated upload was not refused"
conn.close()
assert set(os.listdir(recv_dir)) == before, "an unauthenticated request created a file"
print("NO-SIDE-EFFECT OK: rejected upload created nothing on disk")

for svc in (receiver, sender, attacker):
    svc.stop()
# stop() must actually free the port, or a restart inside one session fails.
rebind = make(45401, name="rebind")
assert rebind.start(), "port was not released by stop()"
rebind.stop()
print("SHUTDOWN OK: listening port released")

shutil.rmtree(tmp, ignore_errors=True)
print("\nALL FILESHARE TESTS PASSED")
