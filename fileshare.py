"""LAN file transfer: find the other machine over UDP broadcast, push files over HTTP.

Stdlib only, deliberately. The app ships as a PyInstaller bundle and every extra
dependency is one more thing that can fail to freeze; mDNS/zeroconf would give
nicer discovery than a UDP broadcast, but not enough to justify the packaging
risk on a two-machine LAN.

Security model: discovery is **not** authentication — anyone on the Wi-Fi can
announce themselves. A shared pairing token is what gates writes to your disk,
and it is never put on the wire: the sender asks for a nonce and returns
HMAC(token, nonce), so impersonating a peer to harvest the token does not work
and a captured proof cannot be replayed. Requests that fail the check get 403
and nothing touches the filesystem.

The traffic itself is plain HTTP. That is a documented limitation, not an
oversight: it keeps the implementation small enough to audit, and the threat
this feature actually defends against is a stranger on the café Wi-Fi writing
files to your disk, not one reading a file you chose to send.
"""
import errno
import hashlib
import hmac
import http.client
import json
import os
import secrets
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

from PySide6.QtCore import QObject, Signal

# Deliberately below 49152, the start of the Windows dynamic port range. Windows
# hands large blocks in that range to Hyper-V/WSL as "excluded port ranges", and
# binding inside one fails with WinError 10013 — which is how the first choice
# here (53317/53318) died on the Windows box: 53318 landed inside 53318-53417.
# Worse, those blocks are re-drawn on every reboot, so a port that works today
# can be unbindable tomorrow. Below 49152 the machine only reserved 5985 and
# 47001, and nothing re-draws it.
DISCOVERY_PORT = 45317
ANNOUNCE_INTERVAL = 5.0
# Three missed announces before a peer disappears, so one dropped broadcast
# (common on Wi-Fi) doesn't make the menu flicker.
PEER_TTL = ANNOUNCE_INTERVAL * 3 + 2
CHUNK = 256 * 1024
# Transfers are LAN-local; a stall this long means the peer went away. Applied
# to accepted sockets too, otherwise a peer that opens a connection and dribbles
# headers forever pins a handler thread (slowloris) without ever authenticating.
SOCKET_TIMEOUT = 30.0
PROTOCOL_VERSION = 2

# A nonce is only useful for the moment between asking and uploading.
NONCE_TTL = 30.0
# Caps below exist so an unauthenticated LAN host cannot make us consume
# unbounded memory, threads, disk or log space just by sending packets.
MAX_NONCES = 64
MAX_PEERS = 32
MAX_ACTIVE_RECEIVES = 4
MAX_ACTIVE_SENDS = 3
# Deterministic neighbours to try before letting the OS pick, so a hand-written
# peer entry can be pointed one port up and survive a restart.
PORT_FALLBACK_TRIES = 8
# Refuse an upload that would leave the disk this close to full.
DISK_RESERVE = 256 * 1024 * 1024
# An unpaired flood must not be able to grow app.log without bound.
DISCOVERY_LOG_BUDGET = 20
DISCOVERY_LOG_WINDOW = 60.0

_WIN_FORBIDDEN = '<>:"|?*'
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _truncate_bytes(name, limit):
    """Trim to `limit` UTF-8 *bytes*, keeping the extension, never splitting a char.

    Truncating by characters is not enough: APFS and NTFS both bound a path
    component in bytes, so a 180-character CJK name is ~540 bytes and still fails
    to create.
    """
    if len(name.encode("utf-8")) <= limit:
        return name
    base, ext = os.path.splitext(name)
    ext = ext.encode("utf-8")[: limit // 2].decode("utf-8", "ignore")
    room = limit - len(ext.encode("utf-8"))
    base = base.encode("utf-8")[:room].decode("utf-8", "ignore")
    return (base + ext) or "received_file"


def _safe_filename(raw):
    """Reduce a peer-supplied name to something safe to create on either platform.

    os.path.basename() alone is not enough. It is platform-specific, so a Windows
    peer sending r"..\\..\\evil.exe" would come through untouched on macOS and
    escape the download directory; and a name macOS accepts can still be illegal
    or dangerous on Windows (reserved device names, NTFS ":" stream syntax).
    """
    name = unquote(raw or "").strip()
    # Normalise Windows separators *before* basename() so the last component is
    # found whichever platform the peer runs on, and so a legitimate name is not
    # mangled into "_.._etc_passwd" just to make it safe.
    name = name.replace("\\", "/").rstrip("/")
    name = os.path.basename(name).strip()
    # Leading dots would write hidden files (.bashrc, .ssh/...).
    name = name.lstrip(".")
    # Control characters, NUL, and everything Windows refuses. ":" has to go even
    # for a macOS receiver, or the file becomes unreadable once copied to NTFS.
    name = "".join("_" if ch in _WIN_FORBIDDEN or ord(ch) < 32 else ch for ch in name)
    # Windows silently drops trailing dots and spaces, so a name ending in one
    # would land under a different filename than the one we report.
    name = name.rstrip(". ")
    if name.split(".")[0].upper() in _WIN_RESERVED:
        name = "_" + name
    if not name:
        name = "received_file"
    return _truncate_bytes(name, 180)


def _unique_open(directory, name):
    """Create-and-open `name` in `directory`, adding " (n)" until the name is free.

    O_EXCL rather than an exists() check, so two transfers landing at the same
    moment cannot silently overwrite each other. O_NOFOLLOW stops a pre-planted
    symlink in the download directory from redirecting the write elsewhere.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    base, ext = os.path.splitext(name)
    for i in range(0, 1000):
        candidate = name if i == 0 else f"{base} ({i}){ext}"
        path = os.path.join(directory, candidate)
        try:
            return os.open(path, flags, 0o600), path
        except FileExistsError:
            continue
        except OSError as exc:
            if exc.errno == errno.ENAMETOOLONG and len(base) > 40:
                base = base[:40]
                continue
            raise
    raise OSError(f"could not find a free filename for {name!r}")


def is_lan_ip(ip):
    """True for RFC1918 addresses — the only ones a peer can actually reach us on."""
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def local_ip():
    """Best-effort LAN address of this machine.

    Connecting a UDP socket sends nothing but makes the OS pick the interface it
    would really use, which beats gethostbyname(gethostname()) — that returns
    127.0.0.1 on many Linux setups.

    The public-DNS probe is tried *last* on purpose: when a proxy runs in TUN
    mode it owns the default route and hands back a 198.18.x.x fake-ip address,
    which is useless for telling a peer where to reach us. Probing private
    targets first makes the OS resolve a route over the real LAN interface.
    """
    for target in ("192.168.0.1", "192.168.1.1", "10.0.0.1", "172.16.0.1", "8.8.8.8"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 53))
            ip = s.getsockname()[0]
            if is_lan_ip(ip):
                return ip
        except OSError:
            continue
        finally:
            s.close()
    return "127.0.0.1"


def parse_peer(entry, default_port):
    """Turn a "host" or "host:port" config string into a peer dict, or None."""
    if not isinstance(entry, str) or not entry.strip():
        return None
    host, _, port = entry.strip().partition(":")
    host = host.strip()
    if not host:
        return None
    try:
        port = int(port) if port else default_port
    except ValueError:
        return None
    if not (0 < port < 65536):
        return None
    return {"id": f"manual:{host}:{port}", "name": host, "host": host, "port": port,
            "manual": True}


def proof_for(token, nonce):
    """The value a sender must present for `nonce`. Never transmits the token."""
    return hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 so Content-Length framing is honoured and the sender can reuse
    # the connection; the default 1.0 closes after every response.
    protocol_version = "HTTP/1.1"
    # socketserver applies this to the accepted socket. Without it an unpaired
    # host can hold handler threads open indefinitely by never finishing a
    # request, and no amount of auth checking helps because auth is never reached.
    timeout = SOCKET_TIMEOUT

    def log_message(self, fmt, *args):
        self.server.service._log(f"http {self.address_string()} {fmt % args}")

    def handle_one_request(self):
        # A timed-out or reset connection is normal on a LAN; letting it reach
        # socketserver prints a traceback per event and tells the user nothing.
        try:
            super().handle_one_request()
        except (socket.timeout, TimeoutError, ConnectionError, OSError):
            self.close_connection = True

    def _reply(self, code, body=b""):
        if code >= 400:
            # The request body has not been drained, so whatever is still in
            # flight would be parsed as a bogus follow-up request (this showed up
            # as spurious 414s). Close rather than try to reuse the connection.
            self.close_connection = True
        try:
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (OSError, ConnectionError):
            self.close_connection = True

    def _authorised(self):
        service = self.server.service
        return service.check_proof(self.headers.get("X-Nonce", ""),
                                   self.headers.get("X-Auth", ""))

    def do_GET(self):
        """Hand out a nonce. Cheap on purpose — it must not be an amplifier."""
        if self.path != "/challenge":
            self._reply(404, b"no such endpoint")
            return
        nonce = self.server.service.new_nonce()
        if nonce is None:
            self._reply(503, b"too many pending challenges")
            return
        self._reply(200, nonce.encode("ascii"))

    def do_POST(self):
        service = self.server.service
        if self.path == "/auth":
            # Zero-byte pairing check. Without it an unpaired sender only finds
            # out mid-upload: the receiver rejects after the headers, the sender
            # dies on a broken pipe, and the real reason (403) never surfaces.
            # The reply carries the next nonce so this costs one round trip.
            if not self._authorised():
                service._log(f"failed pairing check from {self.address_string()}")
                self._reply(403, b"pairing code mismatch")
                return
            nxt = service.new_nonce()
            if nxt is None:
                self._reply(503, b"too many pending challenges")
                return
            self._reply(200, nxt.encode("ascii"))
            return

        if self.path != "/recv":
            self._reply(404, b"no such endpoint")
            return

        if not self._authorised():
            service._log(f"rejected transfer from {self.address_string()}: bad pairing proof")
            self._reply(403, b"pairing code mismatch")
            return

        try:
            total = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            self._reply(400, b"bad Content-Length")
            return
        if total < 0:
            self._reply(400, b"missing Content-Length")
            return

        name = _safe_filename(self.headers.get("X-Filename", ""))
        sender = unquote(self.headers.get("X-Sender", "") or self.address_string())
        directory = service.recv_dir

        # Bound how much work concurrent uploads can do at once. Past the cap we
        # refuse rather than queue, so the sender learns immediately.
        if not service.receive_slot.acquire(blocking=False):
            self._reply(503, b"too many transfers in progress")
            return
        try:
            try:
                os.makedirs(directory, exist_ok=True)
                free = shutil.disk_usage(directory).free
            except OSError as exc:
                service._log(f"cannot prepare {directory!r}: {exc}")
                self._reply(500, b"cannot write to download directory")
                return
            # A paired peer is trusted, not infallible: refuse an upload that
            # would fill the disk rather than wedge the machine.
            if total > max(0, free - DISK_RESERVE):
                service._log(f"refused {name!r}: {total} bytes, only {free} free")
                self._reply(507, b"not enough free space")
                service.failed.emit(f"拒收 {name}：磁盘空间不足")
                return

            try:
                fd, path = _unique_open(directory, name)
            except OSError as exc:
                service._log(f"cannot open destination for {name!r}: {exc}")
                self._reply(500, b"cannot create file")
                return

            # Stream to disk. Reading Content-Length in one shot would hold the
            # whole file in memory, which is what makes a clipboard-shaped
            # transfer unusable for big files.
            got = 0
            try:
                with os.fdopen(fd, "wb") as f:
                    while got < total:
                        block = self.rfile.read(min(CHUNK, total - got))
                        if not block:
                            raise ConnectionError("peer closed mid-transfer")
                        f.write(block)
                        got += len(block)
                        service.progress.emit(name, got, total, False)
            except (OSError, ConnectionError, socket.timeout, TimeoutError) as exc:
                service._log(f"receive of {name!r} failed after {got}/{total} bytes: {exc}")
                try:
                    os.unlink(path)
                except OSError:
                    pass
                self._reply(500, b"transfer interrupted")
                service.failed.emit(f"接收 {name} 失败：{exc}")
                return
        finally:
            service.receive_slot.release()

        service._log(f"received {path} ({got} bytes) from {sender}")
        self._reply(200, b"ok")
        service.received.emit(path, sender)


class FileShareService(QObject):
    """Tray-facing facade: owns the HTTP receiver, the discovery thread and sends.

    Signals cross from worker threads into the GUI thread. That is safe even
    though the tray handlers are plain methods on a non-QObject: PySide6 gives
    the internal receiver the *sender's* thread affinity, and this object is
    constructed on the main thread, so an AutoConnection emit from a worker is
    queued rather than run inline. Verified empirically — do not "fix" this by
    emitting from a thread that also owns this object.
    """

    peers_changed = Signal(list)          # [{"name","host","port","id"}, ...]
    received = Signal(str, str)           # path, sender name
    # qint64, not int: Qt's int is 32-bit, and a >2GiB size silently arrives as a
    # negative number (shiboken only warns), which would break progress reporting
    # on exactly the large files this feature exists to move.
    progress = Signal(str, "qint64", "qint64", bool)  # name, done, total, is_send
    failed = Signal(str)
    sent = Signal(str, str)               # name, peer name

    def __init__(self, config, log=None, parent=None):
        super().__init__(parent)
        self.config = config
        self._log_fn = log or (lambda _msg: None)
        self.token = config.get("fileshare_token") or ""
        self.recv_dir = config["fileshare_recv_dir"]
        self.name = config.get("fileshare_name") or socket.gethostname()
        # A per-process id, not a stable machine id: it only has to be unique
        # enough to filter our own broadcasts out of the peer list.
        self.id = secrets.token_hex(8)

        # Always-offered fallback peers. UDP broadcast does not survive every
        # network — this machine runs a TUN-mode proxy that owns the default
        # route — and a peer you can name by IP should not be hidden just
        # because its announce never arrived.
        default_port = int(config["fileshare_port"])
        self._manual_peers = [
            p for p in (parse_peer(e, default_port) for e in config.get("fileshare_peers") or [])
            if p is not None
        ]

        self._server = None
        self._threads = []
        self._threads_lock = threading.Lock()
        self._peers = {}
        self._peers_lock = threading.Lock()
        self._nonces = {}
        self._nonce_lock = threading.Lock()
        # Announce socket, pinned to the LAN interface — see _tx_socket().
        self._tx = None
        self._tx_ip = None
        self._stop = threading.Event()
        self.receive_slot = threading.Semaphore(MAX_ACTIVE_RECEIVES)
        # Bounded upload pool: the queue holds the backlog, never a thread each.
        self._send_queue = []
        self._send_workers = []
        self._send_lock = threading.Lock()
        self._log_budget = DISCOVERY_LOG_BUDGET
        self._log_window_start = time.monotonic()
        self.port = 0

    def _log(self, msg):
        try:
            self._log_fn(msg)
        except Exception:  # logging must never take the transfer down
            pass

    def _log_discovery(self, msg):
        """Rate-limited logging for anything an unauthenticated peer can trigger.

        Without this, a UDP flood of forged ids appends to app.log without bound.
        """
        now = time.monotonic()
        if now - self._log_window_start > DISCOVERY_LOG_WINDOW:
            self._log_window_start = now
            self._log_budget = DISCOVERY_LOG_BUDGET
        if self._log_budget <= 0:
            return
        self._log_budget -= 1
        if self._log_budget == 0:
            msg += " (further discovery logging suppressed for a minute)"
        self._log(msg)

    # -------------------------------------------------------------------- auth

    def new_nonce(self):
        """Mint a single-use challenge. Returns None when too many are pending."""
        now = time.monotonic()
        nonce = secrets.token_hex(16)
        with self._nonce_lock:
            expired = [n for n, exp in self._nonces.items() if exp <= now]
            for n in expired:
                del self._nonces[n]
            if len(self._nonces) >= MAX_NONCES:
                return None
            self._nonces[nonce] = now + NONCE_TTL
        return nonce

    def check_proof(self, nonce, proof):
        """Verify HMAC(token, nonce) and burn the nonce so it cannot be replayed."""
        if not self.token or not nonce or not proof:
            return False
        with self._nonce_lock:
            expiry = self._nonces.pop(nonce, None)
        if expiry is None or expiry <= time.monotonic():
            return False
        # compare_digest so a wrong proof is not distinguishable by timing.
        return hmac.compare_digest(proof, proof_for(self.token, nonce))

    # ---------------------------------------------------------------- lifecycle

    def _bind(self, port):
        try:
            return ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        except OSError as exc:
            self._log(f"fileshare: cannot bind port {port}: {exc}")
            return None

    def start(self):
        """Bind the receiver and start announcing. Returns True if reachable."""
        port = int(self.config["fileshare_port"])
        self._server = self._bind(port)
        if self._server is None:
            # Do not give up on one number. Windows re-draws its Hyper-V/WSL port
            # reservations on every reboot, so the configured port can become
            # unbindable overnight through no fault of the user.
            #
            # Walk a short deterministic range before asking the OS for anything
            # free: a hand-written fileshare_peers entry can be pointed at
            # port+1 and stay correct across restarts, which a random high port
            # never would. Discovered peers are fine either way — the announce
            # carries whichever port we ended up on.
            for candidate in [port + i for i in range(1, PORT_FALLBACK_TRIES + 1)] + [0]:
                self._server = self._bind(candidate)
                if self._server is not None:
                    break
            if self._server is not None:
                actual = self._server.server_address[1]
                self._log(f"fileshare: port {port} unavailable, fell back to {actual}")
                self.failed.emit(
                    f"传文件端口 {port} 不可用，已自动改用 {actual}。"
                    f"自动发现不受影响；若对端是手动写死 IP 的，需要补上 :{actual}。"
                )
        if self._server is None:
            self.failed.emit(f"传文件端口 {port} 不可用，且系统未分配到备用端口，收文件功能未启动。")
            return False

        self._server.service = self
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]

        self._spawn(self._server.serve_forever, "fileshare-http")
        self._spawn(self._discovery_loop, "fileshare-discovery")
        self._log(f"fileshare listening on {local_ip()}:{self.port} as {self.name!r}")
        return True

    def _spawn(self, target, name):
        t = threading.Thread(target=target, name=name, daemon=True)
        with self._threads_lock:
            # Drop finished threads first, or a long tray session accumulates a
            # Thread object per file ever sent.
            self._threads = [x for x in self._threads if x.is_alive()]
            self._threads.append(t)
        t.start()

    def stop(self):
        self._stop.set()
        server, self._server = self._server, None
        if server is not None:
            def shutdown():
                # shutdown() blocks until serve_forever returns, so it must not
                # run on the serving thread itself. server_close() releases the
                # listening socket — without it the port can linger bound for
                # the life of the process.
                try:
                    server.shutdown()
                finally:
                    server.server_close()

            t = threading.Thread(target=shutdown, name="fileshare-stop", daemon=True)
            t.start()
            # Brief join so a normal quit really does free the port; a wedged
            # handler must not be able to hold the app open, hence the timeout.
            t.join(timeout=2.0)

    # ---------------------------------------------------------------- discovery

    def _discovery_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError as exc:
            self._log(f"fileshare: discovery port busy ({exc}); peers must be added by IP")
            sock.close()
            return
        # Wake up regularly enough to re-announce and to expire stale peers even
        # when nothing is being received.
        sock.settimeout(1.0)

        try:
            last_announce = 0.0
            while not self._stop.is_set():
                now = time.monotonic()
                if now - last_announce >= ANNOUNCE_INTERVAL:
                    self._announce(sock)
                    last_announce = now
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    self._expire(now)
                    continue
                except OSError:
                    break
                self._on_announce(data, addr)
                self._expire(time.monotonic())
        finally:
            sock.close()
            if self._tx is not None:
                self._tx.close()
                self._tx = None

    def _tx_socket(self):
        """Announce socket pinned to the LAN interface.

        The receive socket is bound to every interface, so sending from it lets
        the routing table pick the egress — and on a machine with several
        interfaces that choice is usually wrong. The Windows box here has five
        (Docker, WSL, VMware) and its limited broadcast never reached the LAN,
        so the Mac never saw it while the Mac's own announces got through.
        Binding the sender to the LAN address pins the interface.
        """
        # Resolved every announce, not cached on a timer: local_ip() only does
        # route lookups (connect() on UDP sends nothing), and a cached socket
        # bound to an address that DHCP took away goes quiet for the whole cache
        # window — longer than PEER_TTL, so peers vanish before it recovers.
        ip = local_ip()
        if self._tx is not None and ip == self._tx_ip:
            return self._tx, ip
        if self._tx is not None:
            self._tx.close()
            self._tx = None
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            s.bind((ip, 0))
        except OSError:
            # Address vanished (DHCP change, cable pulled). Announcing from an
            # unpinned socket is still better than not announcing at all.
            s.close()
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            ip = None
        self._tx, self._tx_ip = s, ip
        return s, ip

    def _announce(self, _sock):
        msg = json.dumps({
            "v": PROTOCOL_VERSION, "id": self.id, "name": self.name, "port": self.port,
        }).encode("utf-8")
        sock, ip = self._tx_socket()
        # Limited broadcast first; the subnet-directed one is a second chance for
        # networks that drop 255.255.255.255. A /24 guess is safe here because it
        # is only an extra packet — the limited broadcast still covers the rest.
        targets = ["255.255.255.255"]
        if ip and is_lan_ip(ip):
            targets.append(ip.rsplit(".", 1)[0] + ".255")
        for target in targets:
            try:
                sock.sendto(msg, (target, DISCOVERY_PORT))
            except OSError as exc:
                self._log_discovery(f"fileshare: announce to {target} failed: {exc}")

    def _on_announce(self, data, addr):
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return  # some other program shares this port; ignore it quietly
        if not isinstance(msg, dict) or msg.get("v") != PROTOCOL_VERSION:
            return
        peer_id = msg.get("id")
        port = msg.get("port")
        if not isinstance(peer_id, str) or peer_id == self.id or len(peer_id) > 64:
            return
        if not isinstance(port, int) or not (0 < port < 65536):
            return
        name = msg.get("name")
        if not isinstance(name, str) or not name.strip():
            name = addr[0]

        with self._peers_lock:
            known = peer_id in self._peers
            # Anyone can announce, so the table has to be bounded or a flood of
            # forged ids eats memory and freezes the menu.
            if not known and len(self._peers) >= MAX_PEERS:
                return
            self._peers[peer_id] = {
                "id": peer_id, "name": name[:60], "host": addr[0],
                "port": port, "seen": time.monotonic(),
            }
        if not known:
            self._log_discovery(f"fileshare: discovered {name[:60]!r} at {addr[0]}:{port}")
            self._emit_peers()

    def _expire(self, now):
        with self._peers_lock:
            dead = [k for k, v in self._peers.items() if now - v["seen"] > PEER_TTL]
            for k in dead:
                del self._peers[k]
        if dead:
            self._log_discovery(f"fileshare: lost {len(dead)} peer(s)")
            self._emit_peers()

    def _emit_peers(self):
        self.peers_changed.emit(self.peers())

    def peers(self):
        with self._peers_lock:
            found = {p["host"]: dict(p) for p in self._peers.values()}
        # A discovered peer wins over a manual entry for the same host, so the
        # menu shows the real machine name rather than a bare IP.
        for entry in self._manual_peers:
            found.setdefault(entry["host"], dict(entry))
        return sorted(found.values(), key=lambda p: p["name"])

    # --------------------------------------------------------------------- send

    def send(self, path, peer):
        """Queue one file for upload. Returns immediately, never blocks the GUI.

        Work goes to a fixed pool rather than a thread per file: selecting a
        thousand files in the dialog would otherwise create a thousand threads
        that immediately block on a semaphore, and Thread.start() can raise on
        the GUI thread once the OS runs out.
        """
        with self._send_lock:
            self._send_queue.append((path, peer))
            starved = len(self._send_workers) < min(MAX_ACTIVE_SENDS, len(self._send_queue))
            if starved:
                self._send_workers.append(True)
                spawn = True
            else:
                spawn = False
        if spawn:
            self._spawn(self._send_worker, "fileshare-send")

    def _send_worker(self):
        while True:
            with self._send_lock:
                if not self._send_queue:
                    self._send_workers.pop()
                    return
                path, peer = self._send_queue.pop(0)
            self._send_one(path, os.path.basename(path), peer)

    def _send_one(self, path, name, peer):
        try:
            total = os.path.getsize(path)
        except OSError as exc:
            self.failed.emit(f"读取 {name} 失败：{exc}")
            return

        conn = http.client.HTTPConnection(peer["host"], peer["port"], timeout=SOCKET_TIMEOUT)
        try:
            # Handshake: fetch a nonce, prove we hold the token against it, and
            # get the nonce for the upload back in the same reply. The token
            # itself never goes on the wire, and an unpaired sender is turned
            # away before it streams a byte.
            conn.request("GET", "/challenge")
            resp = conn.getresponse()
            nonce = resp.read(256).decode("ascii", "replace").strip()
            if resp.status != 200 or not nonce:
                self.failed.emit(f"无法发送到 {peer['name']}：对方拒绝握手（HTTP {resp.status}）")
                return

            conn.request("POST", "/auth", body=b"", headers={
                "X-Nonce": nonce, "X-Auth": proof_for(self.token, nonce),
                "Content-Length": "0",
            })
            resp = conn.getresponse()
            nonce = resp.read(256).decode("ascii", "replace").strip()
            if resp.status == 403:
                self.failed.emit(f"无法发送到 {peer['name']}：配对码不一致")
                self._log(f"pairing check against {peer['name']} failed")
                return
            if resp.status != 200 or not nonce:
                self.failed.emit(f"无法发送到 {peer['name']}：握手失败（HTTP {resp.status}）")
                return

            conn.putrequest("POST", "/recv", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"{peer['host']}:{peer['port']}")
            # Headers must be latin-1-safe, so anything non-ASCII is percent-encoded.
            conn.putheader("X-Filename", quote(name))
            conn.putheader("X-Sender", quote(self.name))
            conn.putheader("X-Nonce", nonce)
            conn.putheader("X-Auth", proof_for(self.token, nonce))
            conn.putheader("Content-Length", str(total))
            conn.putheader("Content-Type", "application/octet-stream")
            conn.endheaders()

            done = 0
            with open(path, "rb") as f:
                while done < total:
                    # Never read past the size we declared. Reading to EOF would
                    # push extra bytes at a receiver that stopped listening after
                    # Content-Length, and those bytes get parsed as a new request.
                    block = f.read(min(CHUNK, total - done))
                    if not block:
                        raise OSError(
                            f"file shrank while sending ({done} of {total} bytes)")
                    conn.send(block)
                    done += len(block)
                    self.progress.emit(name, done, total, True)

            resp = conn.getresponse()
            body = resp.read(2048).decode("utf-8", "replace").strip()
            if resp.status != 200:
                hint = "配对码不一致" if resp.status == 403 else (body or resp.reason)
                self.failed.emit(f"发送 {name} 被拒绝（{resp.status}）：{hint}")
                self._log(f"send {name!r} rejected: {resp.status} {body}")
                return
        except (OSError, http.client.HTTPException) as exc:
            # The receiver may have refused mid-stream (disk full, bad request)
            # and hung up on us. Its response explains far more than "broken
            # pipe", so try to read it before falling back to the socket error.
            reason = str(exc)
            try:
                # Only worth asking when a socket actually exists. On a refused
                # connection there is nothing to read, and the half-built
                # HTTPResponse this leaves behind raises from its own close()
                # during GC ("Exception ignored ... no attribute 'fp'").
                if conn.sock is not None:
                    resp = conn.getresponse()
                    body = resp.read(2048).decode("utf-8", "replace").strip()
                    reason = f"{resp.status} {body or resp.reason}"
            except Exception:
                # Best-effort diagnosis only. Reading a response off a connection
                # that already failed can raise almost anything — http.client
                # surfaces a bare AttributeError when the socket died before the
                # response object was wired up — and none of it must replace the
                # real error or escape into the worker thread.
                pass
            self.failed.emit(f"发送 {name} 失败：{reason}")
            self._log(f"send {name!r} to {peer['host']}:{peer['port']} failed: {reason}")
            return
        finally:
            conn.close()

        self._log(f"sent {path} ({total} bytes) to {peer['name']}")
        self.sent.emit(name, peer["name"])


def new_token():
    """A fresh pairing code. Short enough to retype, long enough to not guess."""
    return secrets.token_hex(8)
