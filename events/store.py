"""Small Firestore wrapper.

Every read/write in the app goes through these functions, so the same code
runs against real Firestore (firebase-admin) or an in-memory fake used by the
tests and by local demo mode (FIRESTORE_FAKE=true).

Paths are plain strings: "users", "users/{uid}/tixProfileHistory".
Sentinels: INC(n), ARRAY_UNION([...]), SERVER_TS, DELETE.
Update keys may be dotted field paths: {"tixUserData.readNotifs.abc": True}.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import secrets
import threading
from pathlib import Path

from django.conf import settings

# --------------------------------------------------------------------------- #
# Sentinels (mapped to firestore ones in the real backend)
# --------------------------------------------------------------------------- #


class _Inc:
    def __init__(self, n):
        self.n = n


class _Union:
    def __init__(self, values):
        self.values = list(values)


class _Sentinel:
    def __init__(self, name):
        self.name = name


def INC(n):
    return _Inc(n)


def ARRAY_UNION(values):
    return _Union(values)


SERVER_TS = _Sentinel("SERVER_TS")
DELETE = _Sentinel("DELETE")


def now():
    return dt.datetime.now(dt.timezone.utc)


def new_id(n=20):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


# --------------------------------------------------------------------------- #
# Backend selection
# --------------------------------------------------------------------------- #

_backend = None
_lock = threading.Lock()


def backend():
    global _backend
    with _lock:
        if _backend is None:
            _backend = FakeBackend() if settings.FIRESTORE_FAKE else RealBackend()
        return _backend


def reset_fake(data=None):
    """Tests: start from an empty (or given) fake database."""
    global _backend
    _backend = FakeBackend(persist=False)
    if data:
        _backend.docs = copy.deepcopy(data)
    return _backend


# Public API ---------------------------------------------------------------- #


def get(col, doc_id):
    return backend().get(col, doc_id)


def where(col, filters=(), order_by=None, descending=False, limit=None):
    """filters: [(field, op, value)], op in == != > >= < <= array_contains array_contains_any in"""
    return backend().where(col, list(filters), order_by, descending, limit)


def all_docs(col):
    return backend().where(col, [], None, False, None)


def set(col, doc_id, data, merge=False):  # noqa: A001 - mirrors Firestore
    backend().set(col, doc_id, data, merge)


def update(col, doc_id, data):
    backend().update(col, doc_id, data)


def add(col, data):
    doc_id = new_id()
    backend().set(col, doc_id, data, False)
    return doc_id


def batch(ops):
    """ops: [("set"|"update", col, id, data, merge)] committed together."""
    backend().batch(ops)


def transaction(fn):
    """fn(tx) -> result. tx.get(col,id), tx.set(col,id,data,merge=False), tx.update(col,id,data)."""
    return backend().transaction(fn)


# --------------------------------------------------------------------------- #
# Real Firestore
# --------------------------------------------------------------------------- #


class RealBackend:
    def __init__(self):
        from firebase_admin import firestore

        from . import firebase

        self.fs = firestore
        self.client = firestore.client(app=firebase.get_app())

    def _conv(self, value):
        if isinstance(value, _Inc):
            return self.fs.Increment(value.n)
        if isinstance(value, _Union):
            return self.fs.ArrayUnion(value.values)
        if value is SERVER_TS:
            return self.fs.SERVER_TIMESTAMP
        if value is DELETE:
            return self.fs.DELETE_FIELD
        if isinstance(value, dict):
            return {k: self._conv(v) for k, v in value.items()}
        return value

    def _ref(self, col, doc_id):
        return self.client.collection(col).document(doc_id)

    def get(self, col, doc_id):
        snap = self._ref(col, doc_id).get()
        return snap.to_dict() if snap.exists else None

    def where(self, col, filters, order_by, descending, limit):
        from google.cloud.firestore_v1.base_query import FieldFilter

        q = self.client.collection(col)
        for field, op, value in filters:
            q = q.where(filter=FieldFilter(field, op, value))
        if order_by:
            q = q.order_by(order_by, direction="DESCENDING" if descending else "ASCENDING")
        if limit:
            q = q.limit(limit)
        return [(d.id, d.to_dict()) for d in q.stream()]

    def set(self, col, doc_id, data, merge):
        self._ref(col, doc_id).set(self._conv(data), merge=merge)

    def update(self, col, doc_id, data):
        self._ref(col, doc_id).update(self._conv(data))

    def batch(self, ops):
        b = self.client.batch()
        for kind, col, doc_id, data, merge in ops:
            ref = self._ref(col, doc_id)
            if kind == "set":
                b.set(ref, self._conv(data), merge=merge)
            else:
                b.update(ref, self._conv(data))
        b.commit()

    def transaction(self, fn):
        backend = self
        tx = self.client.transaction()

        class Tx:
            def get(self, col, doc_id):
                snap = backend._ref(col, doc_id).get(transaction=tx)
                return snap.to_dict() if snap.exists else None

            def set(self, col, doc_id, data, merge=False):
                tx.set(backend._ref(col, doc_id), backend._conv(data), merge=merge)

            def update(self, col, doc_id, data):
                tx.update(backend._ref(col, doc_id), backend._conv(data))

        @self.fs.transactional
        def run(transaction):
            return fn(Tx())

        return run(tx)


# --------------------------------------------------------------------------- #
# In-memory fake (tests + demo mode)
# --------------------------------------------------------------------------- #


class NotFound(Exception):
    pass


def _get_path(d, parts):
    for p in parts:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


class FakeBackend:
    def __init__(self, persist=True):
        self.docs: dict[str, dict[str, dict]] = {}
        self.persist_path = Path(settings.BASE_DIR) / "fake_firestore.json" if persist else None
        self.lock = threading.RLock()
        if self.persist_path and self.persist_path.exists():
            self.docs = json.loads(self.persist_path.read_text(), object_hook=_decode)

    # helpers
    def _save(self):
        if self.persist_path:
            self.persist_path.write_text(json.dumps(self.docs, default=_encode, indent=1))

    def _apply(self, target: dict, dotted: str, value, nested_merge=False):
        parts = dotted.split(".")
        cur = target
        for p in parts[:-1]:
            if not isinstance(cur.get(p), dict):
                cur[p] = {}
            cur = cur[p]
        last = parts[-1]
        if value is DELETE:
            cur.pop(last, None)
        elif value is SERVER_TS:
            cur[last] = now()
        elif isinstance(value, _Inc):
            old = cur.get(last) or 0
            cur[last] = old + value.n if isinstance(old, (int, float)) else value.n
        elif isinstance(value, _Union):
            old = list(cur.get(last) or [])
            for v in value.values:
                if v not in old:
                    old.append(v)
            cur[last] = old
        elif isinstance(value, dict) and nested_merge:
            if not isinstance(cur.get(last), dict):
                cur[last] = {}
            for k, v in value.items():
                self._apply(cur[last], k, v, nested_merge=True)
        elif isinstance(value, dict):
            cur[last] = self._resolve(value)
        else:
            cur[last] = copy.deepcopy(value)

    def _resolve(self, data):
        out = {}
        for k, v in data.items():
            if isinstance(v, dict):
                out[k] = self._resolve(v)
            elif v is SERVER_TS:
                out[k] = now()
            elif isinstance(v, _Inc):
                out[k] = v.n
            elif isinstance(v, _Union):
                out[k] = list(v.values)
            elif v is DELETE:
                continue
            else:
                out[k] = copy.deepcopy(v)
        return out

    # API
    def get(self, col, doc_id):
        with self.lock:
            d = self.docs.get(col, {}).get(doc_id)
            return copy.deepcopy(d) if d is not None else None

    def where(self, col, filters, order_by, descending, limit):
        with self.lock:
            rows = []
            for doc_id, d in self.docs.get(col, {}).items():
                if all(self._match(d, f) for f in filters):
                    rows.append((doc_id, copy.deepcopy(d)))
            if order_by:
                rows.sort(key=lambda r: _sort_key(_get_path(r[1], order_by.split("."))), reverse=descending)
            return rows[:limit] if limit else rows

    @staticmethod
    def _match(d, f):
        field, op, value = f
        v = _get_path(d, field.split("."))
        if op == "==":
            return v == value
        if op == "!=":
            return v != value
        if op == "in":
            return v in value
        if op == "array_contains":
            return isinstance(v, list) and value in v
        if op == "array_contains_any":
            return isinstance(v, list) and any(x in v for x in value)
        if v is None:
            return False
        return {">": v > value, ">=": v >= value, "<": v < value, "<=": v <= value}[op]

    def set(self, col, doc_id, data, merge):
        with self.lock:
            coll = self.docs.setdefault(col, {})
            if merge and doc_id in coll:
                for k, v in data.items():
                    self._apply(coll[doc_id], k, v, nested_merge=True)
            else:
                coll[doc_id] = self._resolve(data)
            self._save()

    def update(self, col, doc_id, data):
        with self.lock:
            doc = self.docs.get(col, {}).get(doc_id)
            if doc is None:
                raise NotFound(f"{col}/{doc_id}")
            for k, v in data.items():
                self._apply(doc, k, v)
            self._save()

    def batch(self, ops):
        with self.lock:
            snapshot = copy.deepcopy(self.docs)
            try:
                for kind, col, doc_id, data, merge in ops:
                    (self.set(col, doc_id, data, merge) if kind == "set" else self.update(col, doc_id, data))
            except Exception:
                self.docs = snapshot
                raise

    def transaction(self, fn):
        backend = self

        class Tx:
            def __init__(self):
                self.ops = []

            def get(self, col, doc_id):
                return backend.get(col, doc_id)

            def set(self, col, doc_id, data, merge=False):
                self.ops.append(("set", col, doc_id, data, merge))

            def update(self, col, doc_id, data):
                self.ops.append(("update", col, doc_id, data, False))

        with self.lock:
            tx = Tx()
            result = fn(tx)
            self.batch(tx.ops)
            return result


def _sort_key(v):
    if v is None:
        return (0, 0)
    if isinstance(v, dt.datetime):
        return (1, v.timestamp())
    return (1, v)


def _encode(o):
    if isinstance(o, dt.datetime):
        return {"__dt__": o.isoformat()}
    raise TypeError(type(o))


def _decode(d):
    if "__dt__" in d and len(d) == 1:
        return dt.datetime.fromisoformat(d["__dt__"])
    return d
