"""Look after the field-encryption keys (foundation/keys.py).

    manage.py field_keys status                      where the keys come from, which one is current
    manage.py field_keys new <id> --file ring.json   add a fresh key and make it current
    manage.py field_keys wrap --from plain.json --file wrapped.json
                                                      seal a plain ring under the key-encryption key
    manage.py field_keys split --shares 5 --threshold 3
                                                      split the key ring so a loss is survivable
    manage.py field_keys combine <share> <share> ... --file ring.json
                                                      rebuild a key ring from enough shares

Shares and keys are printed or written only where you tell it to. Nothing here logs a key.
After `new`, run `manage.py rotate_field_keys` to move existing values onto the new key.
"""
from __future__ import annotations

import os
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from foundation import keys


class Command(BaseCommand):
    help = "Show, create, wrap, split and restore the field-encryption key ring."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("action", choices=["status", "new", "wrap", "split", "combine"])
        parser.add_argument("items", nargs="*", help="a new key id (new), or shares (combine)")
        parser.add_argument("--file", help="the key ring file to write (new, wrap, combine)")
        parser.add_argument("--from", dest="source", help="a plain key ring file (wrap)")
        parser.add_argument("--shares", type=int, default=5)
        parser.add_argument("--threshold", type=int, default=3)

    def handle(self, *args: Any, **opts: Any) -> None:
        action = opts["action"]
        try:
            if action == "status":
                self._status()
            elif action == "new":
                self._new(opts)
            elif action == "wrap":
                self._wrap(opts)
            elif action == "split":
                self._split(opts)
            else:
                self._combine(opts)
        except keys.KeyRingError:
            raise CommandError("The key ring could not be read or the shares do not fit.") from None
        except ValueError as exc:
            raise CommandError(str(exc)) from None

    def _say(self, text: str) -> None:
        self.stdout.write(text)

    def _status(self) -> None:
        info = keys.describe()
        self._say(f"provider: {info['provider']}   loaded: {'yes' if info['ok'] else 'NO'}")
        for k in info["keys"]:
            mark = "   <- current" if k["current"] else ""
            self._say(f"  {k['id']:<16} {k['fingerprint']}{mark}")
        for w in info["warnings"]:
            self._say(f"  warning: {w}")

    def _new(self, opts: dict[str, Any]) -> None:
        if not opts["items"] or not opts["file"]:
            raise CommandError("usage: field_keys new <key id> --file <ring file>")
        existing = keys.load_ring().keys if keys.describe()["ok"] else {}
        ring = keys.new_key_ring(opts["items"][0], existing)
        kek = keys._kek() if opts.get("wrap") else None
        keys.write_ring_file(opts["file"], ring, kek=kek)
        self._say(f"Wrote {opts['file']} with {len(ring.keys)} key(s); current is {ring.current}.")
        self._say("Point ZOIKO_FIELD_KEY_FILE at it, restart, then run rotate_field_keys.")

    def _wrap(self, opts: dict[str, Any]) -> None:
        if not opts["source"] or not opts["file"]:
            raise CommandError("usage: field_keys wrap --from <plain ring> --file <wrapped ring>")
        from pathlib import Path

        ring = keys.ring_from_json(Path(opts["source"]).read_text(encoding="utf8"))
        keys.write_ring_file(opts["file"], ring, kek=keys._kek())
        self._say(f"Wrote {opts['file']}: {len(ring.keys)} key(s) sealed under the KEK.")

    def _split(self, opts: dict[str, Any]) -> None:
        ring = keys.load_ring()
        shares = keys.split_secret(
            keys.ring_to_json(keys.KeyRing(ring.keys, ring.current, ring.source)).encode(),
            threshold=opts["threshold"], shares=opts["shares"])
        self._say(f"{len(shares)} shares; any {opts['threshold']} rebuild the key ring. Give each "
                  "to a different person and keep them apart:")
        for s in shares:
            self._say(s)

    def _combine(self, opts: dict[str, Any]) -> None:
        if not opts["items"] or not opts["file"]:
            raise CommandError("usage: field_keys combine <share> <share> ... --file <ring file>")
        ring = keys.ring_from_json(keys.combine_shares(opts["items"]).decode("utf8"))
        if os.path.exists(opts["file"]):
            raise CommandError("that file already exists; choose a new name")
        keys.write_ring_file(opts["file"], ring)
        self._say(f"Rebuilt {len(ring.keys)} key(s) into {opts['file']} (current {ring.current}).")
