"""SQLite metadata registry. Only hashes of independent credentials are stored."""
import hashlib
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

    def revoke_device(self, device_id):
        with self.db:
            self.db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,))
            self.db.execute("UPDATE phones SET revoked=1 WHERE device_id=?", (device_id,))
            self.db.execute("UPDATE pairings SET state='denied' WHERE device_id=?", (device_id,))

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
