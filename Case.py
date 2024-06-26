from __future__ import annotations
from   typing   import Any

from .Exec import Exec
from .Item import Item
from .Pipe import Pipe


class Case(object):

    def __init__(self, e: Exec, n: str, **kw: Any):
        self.__dict__.update(kw)

        self.exec = e
        self.name = n
        self.subs = []

    def __iadd__(self, i: Item) -> Case:
        self.subs.append(Pipe(self, i))
        return self

    def __repr__(self) -> str:
        return self.name

    def __call__(self) -> None:
        for i, p in enumerate(self.subs):
            p.idx = i

    def __getattr__(self, k: str) -> Any:
        return getattr(self.exec, k)
