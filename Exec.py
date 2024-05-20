from __future__ import annotations
from   typing   import Any

import os

from .Case import Case
from .Item import Item


class Exec(object):

    def __init__(self, d: str = '', **kw: Any):
        self.__dict__.update(kw)

        self.dir  = os.path.abspath(d if d else 'out')
        self.subs = []

    def __getattr__(self, k: str) -> Any:
        if k.startswith('set_'):
            return lambda *a, **kw: self.__setattr__(k.replace('set_', ''), *a, **kw)
        else:
            return None

    def __setattr__(self, k: str, *a: Any, **kw: Any) -> Exec:
        if k == 'attr':
            for w, v in kw.items():
                if w not in self.__dict__:
                    self.__dict__[w] = v
        else:
            self.__dict__[k] = a[0] if a else ''

        return self

    def case(self, n: str, **kw: Any) -> Exec:
        self.subs.append(Case(self, n, **kw))
        return self

    def item(self, a: str, **kw: Any) -> Exec:
        case  = self.subs[-1]
        case += Item(case, a, **kw)
        return self

    def pipe(self, a: str, **kw: Any) -> Exec:
        case  = self.subs[-1]
        pipe  = case.subs[-1]
        pipe += Item(case, a, **kw)
        return self

    def done(self, *c: str) -> None:
        try:
            os.mkdir(self.dir, 0o755)
        except FileExistsError:
            pass

        dic = set(c)

        for case in self.subs:
            if case.name not in dic:
                continue

            print(f'case: {case}')
            case()

            for i, item in enumerate(case.subs):
                print(f'  item: {i}: {item}')

                try:
                    item()
                except KeyboardInterrupt:
                    item.done()
                    return
