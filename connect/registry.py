"""SQLite metadata registry. Only hashes of independent credentials are stored."""
import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path


def secret():
    return secrets.token_urlsafe(32)


def digest(value):
    if not isinstance(value, str) or not 16 <= len(value) <= 256:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()


class Registry:
    def __init__(self, path, *, clock=time.time, pair_ttl=300, phone_ttl=2592000,
                 max_devices_per_owner=5, max_phones_per_device=10):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            path.parent.chmod(0o700)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.clock, self.pair_ttl, self.phone_ttl = clock, pair_ttl, phone_ttl
        self.max_devices = max_devices_per_owner
        self.max_phones = max_phones_per_device
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS devices(
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, token_id TEXT NOT NULL,
                name TEXT NOT NULL, secret_hash TEXT UNIQUE NOT NULL,
                created REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pairings(
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
                secret_hash TEXT UNIQUE NOT NULL, claim_hash TEXT UNIQUE,
                name TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS phones(
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(id),
                name TEXT NOT NULL, secret_hash TEXT UNIQUE NOT NULL, csrf TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS push_subscriptions(
                phone_id TEXT PRIMARY KEY REFERENCES phones(id), endpoint_hash TEXT UNIQUE NOT NULL,
                endpoint TEXT NOT NULL, keys_json TEXT NOT NULL,
                requests INTEGER NOT NULL, completion INTEGER NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, expires REAL);
            CREATE TABLE IF NOT EXISTS push_events(
                device_id TEXT NOT NULL REFERENCES devices(id), event_id TEXT NOT NULL,
                created REAL NOT NULL, PRIMARY KEY(device_id,event_id));
            CREATE TABLE IF NOT EXISTS push_preferences(
                phone_id TEXT PRIMARY KEY REFERENCES phones(id),
                requests INTEGER NOT NULL DEFAULT 1, completion INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS push_outbox(
                id INTEGER PRIMARY KEY, phone_id TEXT NOT NULL REFERENCES phones(id),
                event_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, next_attempt REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, UNIQUE(phone_id,event_id));
            CREATE INDEX IF NOT EXISTS push_outbox_due ON push_outbox(next_attempt);
        """)
        self.db.commit()
        if os.name != "nt":
            path.chmod(0o600)

    def close(self):
        self.db.close()

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
            raise ValueError("device/phone name must contain 1 to 80 printable characters")
        return value.strip()

    def register(self, owner_id, token_id, name):
        # These values must only come from the trusted eligibility adapter.
        if (not isinstance(owner_id, int) or isinstance(owner_id, bool) or owner_id <= 0 or
                not isinstance(token_id, int) or isinstance(token_id, bool) or token_id <= 0):
            raise ValueError("invalid backend identity")
        owner_id, token_id = str(owner_id), str(token_id)
        name = self.name(name)
        with self.db:
            count = self.db.execute("SELECT count(*) FROM devices WHERE owner_id=? AND revoked=0", (owner_id,)).fetchone()[0]
            if count >= self.max_devices:
                raise ValueError("device limit reached")
            identifier, token = secret(), secret()
            self.db.execute("INSERT INTO devices(id,owner_id,token_id,name,secret_hash,created) VALUES(?,?,?,?,?,?)",
                            (identifier, owner_id, token_id, name, digest(token), self.clock()))
        return {"deviceId": identifier, "deviceToken": token}

    def device(self, token):
        row = self.db.execute("SELECT * FROM devices WHERE secret_hash=? AND revoked=0", (digest(token),)).fetchone()
        return dict(row) if row else None

    def get_device(self, identifier):
        row = self.db.execute("SELECT * FROM devices WHERE id=? AND revoked=0", (identifier,)).fetchone()
        return dict(row) if row else None

    def pairing(self, device_id):
        now = self.clock()
        with self.db:
            # Outstanding QR links are bounded, expired rows do not accumulate.
            self.db.execute("DELETE FROM pairings WHERE expires<?", (now - 86400,))
            count = self.db.execute("SELECT count(*) FROM pairings WHERE device_id=? AND expires>? AND state IN ('offered','claimed','approved')", (device_id, now)).fetchone()[0]
            if count >= 5:
                raise ValueError("pairing limit reached")
            identifier, token = secret(), secret()
            expires = now + self.pair_ttl
            self.db.execute("INSERT INTO pairings(id,device_id,secret_hash,state,created,expires) VALUES(?,?,?,'offered',?,?)",
                            (identifier, device_id, digest(token), now, expires))
        return {"pairingId": identifier, "token": token, "expires": expires}

    def claim(self, token, name):
        name, now = self.name(name), self.clock()
        with self.db:
            row = self.db.execute("SELECT p.* FROM pairings p JOIN devices d ON d.id=p.device_id WHERE p.secret_hash=? AND p.state='offered' AND p.expires>? AND d.revoked=0", (digest(token), now)).fetchone()
            if not row:
                raise PermissionError("pairing link is invalid, used or expired")
            claim_token = secret()
            self.db.execute("UPDATE pairings SET claim_hash=?,name=?,state='claimed' WHERE id=?", (digest(claim_token), name, row["id"]))
        return {"claimToken": claim_token}

    def pairings(self, device_id):
        rows = self.db.execute("SELECT id,name,state,expires FROM pairings WHERE device_id=? AND expires>? AND state IN ('claimed','approved') ORDER BY created", (device_id, self.clock())).fetchall()
        return [{"id": r["id"], "phoneName": r["name"], "state": r["state"], "expires": r["expires"]} for r in rows]

    def approve(self, device_id, identifier, approved):
        if not isinstance(approved, bool):
            raise ValueError("approved must be boolean")
        with self.db:
            row = self.db.execute("SELECT * FROM pairings WHERE id=? AND device_id=? AND state='claimed' AND expires>?", (identifier, device_id, self.clock())).fetchone()
            if not row:
                raise PermissionError("pairing is not available for this device")
            self.db.execute("UPDATE pairings SET state=? WHERE id=?", ("approved" if approved else "denied", identifier))

    def claim_status(self, claim_token):
        now = self.clock()
        with self.db:
            row = self.db.execute("SELECT p.* FROM pairings p JOIN devices d ON d.id=p.device_id WHERE p.claim_hash=? AND d.revoked=0", (digest(claim_token),)).fetchone()
            if not row:
                raise PermissionError("claim is invalid")
            if row["expires"] <= now:
                return {"state": "expired"}, None
            if row["state"] != "approved":
                return {"state": row["state"]}, None
            count = self.db.execute("SELECT count(*) FROM phones WHERE device_id=? AND revoked=0 AND expires>?", (row["device_id"], now)).fetchone()[0]
            if count >= self.max_phones:
                raise ValueError("phone limit reached")
            identifier, token, csrf = secret(), secret(), secret()
            self.db.execute("INSERT INTO phones(id,device_id,name,secret_hash,csrf,created,expires) VALUES(?,?,?,?,?,?,?)",
                            (identifier, row["device_id"], row["name"], digest(token), csrf, now, now + self.phone_ttl))
            self.db.execute("UPDATE pairings SET state='consumed' WHERE id=?", (row["id"],))
        return {"state": "approved", "csrf": csrf}, token

    def phone(self, token):
        row = self.db.execute("SELECT p.* FROM phones p JOIN devices d ON d.id=p.device_id WHERE p.secret_hash=? AND p.revoked=0 AND p.expires>? AND d.revoked=0", (digest(token), self.clock())).fetchone()
        return dict(row) if row else None

    def phones(self, device_id):
        rows = self.db.execute("SELECT id,name,expires FROM phones WHERE device_id=? AND revoked=0 AND expires>? ORDER BY created", (device_id, self.clock())).fetchall()
        return [dict(row) for row in rows]

    def revoke_phone(self, device_id, identifier):
        with self.db:
            cursor = self.db.execute("UPDATE phones SET revoked=1 WHERE id=? AND device_id=?", (identifier, device_id))
            if cursor.rowcount != 1:
                raise PermissionError("phone does not belong to device")
            self.db.execute("DELETE FROM push_outbox WHERE phone_id=?", (identifier,))
            self.db.execute("DELETE FROM push_subscriptions WHERE phone_id=?", (identifier,))
            self.db.execute("DELETE FROM push_preferences WHERE phone_id=?", (identifier,))

    def revoke_device(self, device_id):
        with self.db:
            self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
            self.db.execute("UPDATE phones SET revoked=1 WHERE device_id=?", (device_id,))
            self.db.execute("UPDATE pairings SET state='denied' WHERE device_id=?", (device_id,))
            self.db.execute("DELETE FROM push_outbox WHERE phone_id IN (SELECT id FROM phones WHERE device_id=?)", (device_id,))
            self.db.execute("DELETE FROM push_subscriptions WHERE phone_id IN (SELECT id FROM phones WHERE device_id=?)", (device_id,))
            self.db.execute("DELETE FROM push_preferences WHERE phone_id IN (SELECT id FROM phones WHERE device_id=?)", (device_id,))
            self.db.execute("DELETE FROM push_events WHERE device_id=?", (device_id,))

    def revoke_backend(self, owner_id=None, token_id=None):
        """Trusted operator hook; not exposed by any public API."""
        if owner_id is None and token_id is None:
            raise ValueError("backend identity required")
        query, parameters = "SELECT id FROM devices WHERE revoked=0", []
        if owner_id is not None:
            query += " AND owner_id=?"
            parameters.append(str(owner_id))
        if token_id is not None:
            query += " AND token_id=?"
            parameters.append(str(token_id))
        identifiers = [r[0] for r in self.db.execute(query, parameters)]
        for identifier in identifiers:
            self.revoke_device(identifier)
        return identifiers

    def get_phone(self, identifier):
        row = self.db.execute("SELECT p.* FROM phones p JOIN devices d ON d.id=p.device_id WHERE p.id=? AND p.revoked=0 AND p.expires>? AND d.revoked=0", (identifier, self.clock())).fetchone()
        return dict(row) if row else None

    def push_preferences(self, phone_id):
        row = self.db.execute("SELECT requests,completion FROM push_preferences WHERE phone_id=?", (phone_id,)).fetchone()
        return {"requests": bool(row["requests"]) if row else True,
                "completion": bool(row["completion"]) if row else True}

    def set_push_preferences(self, phone_id, events):
        with self.db:
            self.db.execute("INSERT INTO push_preferences(phone_id,requests,completion) VALUES(?,?,?) ON CONFLICT(phone_id) DO UPDATE SET requests=excluded.requests,completion=excluded.completion",
                            (phone_id, events["requests"], events["completion"]))
            self.db.execute("UPDATE push_subscriptions SET requests=?,completion=?,updated=? WHERE phone_id=?",
                            (events["requests"], events["completion"], self.clock(), phone_id))
            for kind, enabled in (("request", events["requests"]), ("completion", events["completion"])):
                if not enabled:
                    self.db.execute("DELETE FROM push_outbox WHERE phone_id=? AND kind=?", (phone_id, kind))

    def push_subscription(self, phone_id):
        row = self.db.execute("SELECT * FROM push_subscriptions WHERE phone_id=? AND (expires IS NULL OR expires>?)", (phone_id, self.clock())).fetchone()
        return dict(row) if row else None

    def subscribe_push(self, phone_id, subscription, events):
        now, endpoint = self.clock(), subscription["endpoint"]
        endpoint_hash = hashlib.sha256(endpoint.encode()).hexdigest()
        expires = subscription.get("expirationTime")
        expires = expires / 1000 if expires is not None else None
        with self.db:
            self._prune_push(now)
            owner = self.db.execute("SELECT phone_id FROM push_subscriptions WHERE endpoint_hash=?", (endpoint_hash,)).fetchone()
            if owner and owner[0] != phone_id:
                raise ValueError("push subscription already belongs to another phone")
            # Resubscribing must never deliver events that were queued for old keys.
            self.db.execute("DELETE FROM push_outbox WHERE phone_id=?", (phone_id,))
            self.db.execute("INSERT INTO push_subscriptions(phone_id,endpoint_hash,endpoint,keys_json,requests,completion,created,updated,expires) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(phone_id) DO UPDATE SET endpoint_hash=excluded.endpoint_hash,endpoint=excluded.endpoint,keys_json=excluded.keys_json,requests=excluded.requests,completion=excluded.completion,updated=excluded.updated,expires=excluded.expires",
                            (phone_id, endpoint_hash, endpoint, json.dumps(subscription["keys"], separators=(",", ":")), events["requests"], events["completion"], now, now, expires))
            self.db.execute("INSERT INTO push_preferences(phone_id,requests,completion) VALUES(?,?,?) ON CONFLICT(phone_id) DO UPDATE SET requests=excluded.requests,completion=excluded.completion",
                            (phone_id, events["requests"], events["completion"]))

    def unsubscribe_push(self, phone_id):
        with self.db:
            self.db.execute("DELETE FROM push_outbox WHERE phone_id=?", (phone_id,))
            self.db.execute("DELETE FROM push_subscriptions WHERE phone_id=?", (phone_id,))

    def _prune_push(self, now):
        self.db.execute("DELETE FROM push_outbox WHERE expires<=? OR phone_id IN (SELECT p.id FROM phones p JOIN devices d ON d.id=p.device_id WHERE p.revoked=1 OR p.expires<=? OR d.revoked=1)", (now, now))
        self.db.execute("DELETE FROM push_outbox WHERE phone_id IN (SELECT phone_id FROM push_subscriptions WHERE expires IS NOT NULL AND expires<=?)", (now,))
        self.db.execute("DELETE FROM push_subscriptions WHERE (expires IS NOT NULL AND expires<=?) OR phone_id IN (SELECT p.id FROM phones p JOIN devices d ON d.id=p.device_id WHERE p.revoked=1 OR p.expires<=? OR d.revoked=1)", (now, now))
        self.db.execute("DELETE FROM push_events WHERE created<?", (now - 7 * 86400,))

    def enqueue_push(self, device_id, event, payload):
        """Durable device-scoped deduplication and bounded per-phone delivery queue."""
        now = self.clock()
        with self.db:
            self._prune_push(now)
            cursor = self.db.execute("INSERT OR IGNORE INTO push_events(device_id,event_id,created) VALUES(?,?,?)", (device_id, event["id"], now))
            if not cursor.rowcount:
                return 0
            self.db.execute("DELETE FROM push_events WHERE device_id=? AND event_id NOT IN (SELECT event_id FROM push_events WHERE device_id=? ORDER BY created DESC,rowid DESC LIMIT 8192)", (device_id, device_id))
            ttl = 300 if event["kind"] == "request" else 3600
            expires = min(now + ttl, event["createdAt"] + ttl)
            if expires <= now:
                return 1
            column = "requests" if event["kind"] == "request" else "completion"
            phones = self.db.execute(f"SELECT s.phone_id FROM push_subscriptions s JOIN phones p ON p.id=s.phone_id JOIN devices d ON d.id=p.device_id WHERE p.device_id=? AND p.revoked=0 AND p.expires>? AND d.revoked=0 AND s.{column}=1 AND (s.expires IS NULL OR s.expires>?)", (device_id, now, now)).fetchall()
            for phone in phones:
                self._insert_push(phone[0], event["id"], event["kind"], payload, now, expires)
        return 1

    def _insert_push(self, phone_id, event_id, kind, payload, now, expires):
        self.db.execute("INSERT OR IGNORE INTO push_outbox(phone_id,event_id,kind,payload,created,expires,next_attempt) VALUES(?,?,?,?,?,?,?)", (phone_id, event_id, kind, json.dumps(payload, separators=(",", ":"), ensure_ascii=False), now, expires, now))
        self.db.execute("DELETE FROM push_outbox WHERE phone_id=? AND id NOT IN (SELECT id FROM push_outbox WHERE phone_id=? ORDER BY id DESC LIMIT 256)", (phone_id, phone_id))
        self.db.execute("DELETE FROM push_outbox WHERE id NOT IN (SELECT id FROM push_outbox ORDER BY id DESC LIMIT 8192)")

    def enqueue_push_test(self, phone_id, payload):
        now = self.clock()
        with self.db:
            self._prune_push(now)
            self._insert_push(phone_id, "test:" + secret(), "test", payload, now, now + 300)

    def due_push(self, limit=32):
        now = self.clock()
        with self.db:
            self._prune_push(now)
        rows = self.db.execute("SELECT o.*,s.endpoint,s.endpoint_hash,s.keys_json,p.device_id FROM push_outbox o JOIN push_subscriptions s ON s.phone_id=o.phone_id JOIN phones p ON p.id=o.phone_id WHERE o.next_attempt<=? ORDER BY o.id LIMIT ?", (now, limit)).fetchall()
        return [dict(row) for row in rows]

    def push_pending(self, item):
        # SQLite may reuse a deleted INTEGER PRIMARY KEY. Match event identity
        # as well, so unsubscribe/resubscribe cannot resurrect a stale snapshot.
        return self.db.execute("SELECT 1 FROM push_outbox WHERE id=? AND phone_id=? AND event_id=? AND kind=? AND expires>?", (item["id"], item["phone_id"], item["event_id"], item["kind"], self.clock())).fetchone() is not None

    def finish_push(self, item, *, retry=False):
        identifier = item["id"]
        with self.db:
            owner = self.db.execute("SELECT phone_id,event_id FROM push_outbox WHERE id=?", (identifier,)).fetchone()
            if not owner or owner["phone_id"] != item["phone_id"] or owner["event_id"] != item["event_id"]:
                return
            if retry:
                row = self.db.execute("SELECT attempts,expires FROM push_outbox WHERE id=?", (identifier,)).fetchone()
                attempts = row[0] + 1 if row else 3
                now = self.clock()
                delay = 30 if attempts == 1 else 120
                if row and attempts < 3 and now + delay < row[1]:
                    self.db.execute("UPDATE push_outbox SET attempts=?,next_attempt=? WHERE id=?", (attempts, now + delay, identifier))
                    return
            self.db.execute("DELETE FROM push_outbox WHERE id=?", (identifier,))
