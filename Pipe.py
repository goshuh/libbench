from __future__ import annotations
from   typing   import Any

import os
import signal

from .Item import Item
from .Case import Case


SOUT = -1
NULL = -2


class Pipe(object):
    null = os.open(os.path.join(os.sep, 'dev', 'null'), os.O_RDWR)

    def __init__(self, c: Case, i: Item):
        self.case =  c
        self.subs = [i]
        self.pids = {}
        self.idx  =  0

    def __iadd__(self, i: Item) -> Pipe:
        self.subs.append(i)
        return self

    def __repr__(self) -> str:
        return ' | '.join(map(repr, self.subs))

    def __call__(self) -> None:
        def fd(m: Item, o: int, std: Any):
            if std is None:
                return o
            elif std == NULL:
                return Pipe.null
            elif std == SOUT:
                return m.stdout if o == 2 else Pipe.null
            elif isinstance(std, int):
                return std
            elif isinstance(std, str):
                return os.open(os.path.join(m.cwd if o == 0 else '', std),
                               os.O_WRONLY | os.O_CREAT | os.O_TRUNC if o else os.O_RDONLY, mode=0o644)
            else:
                return std.fileno()

        p = self.subs[ 0]
        p.rt_in  = fd(p, 0, p.stdin)
        c = self.subs[-1]
        c.rt_out = fd(c, 1, os.path.join(self.dir, f'{self.case}-{self.idx}.log') if self.dir else None)
        c.rt_err = fd(c, 2, c.stderr)

        for p, c in zip(self.subs[:-1], self.subs[1:]):
            # ignore user settings
            r, w     = os.pipe()
            c.rt_in  = r
            p.rt_out = w
            p.rt_err = fd(p, 2, p.stderr)
            c.stdiop.append(w)
            p.stdiop.append(r)

        for i, s in enumerate(self.subs):
            if (p := os.fork()) == 0:
                s(self.idx, i)
            else:
                self.pids[p] = True

        for s in self.subs:
            for f in s.stdiop:
                os.close(f)

        while True:
            self.pids.pop(os.waitpid(0, 0)[0], True)
            if not self.pids:
                break

    def done(self) -> None:
        for p in self.pids:
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass

        self.pids = {}

    def __getattr__(self, k: str) -> Any:
        return getattr(self.case, k)
