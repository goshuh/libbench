from __future__ import annotations
from   typing   import Any

import os
import sys
import shlex

from .Case import Case


class Item(object):

    def __init__(self, c: Case, a: str, **kw: Any):
        self.stdin   = 0
        self.stdout  = 1
        self.stderr  = 2

        self.__dict__.update(kw)

        self.case    = c
        self.args    = shlex.split(a)
        self.stdiop  = []

        self.rt_in   = 0
        self.rt_out  = 1
        self.rt_err  = 2
        self.rt_cwd  = []
        self.rt_env  = {}
        self.rt_args = []

    def __repr__(self) -> str:
        return ' '.join(self.args)

    def __call__(self, i: int, j: int) -> None:
        self.rt_cwd  = self.cwd        if isinstance(self.cwd, str ) else ''
        self.rt_env  = self.env.copy() if isinstance(self.env, dict) else {}
        self.rt_args = self.args[::]

        # insert special handlings
        if self.wrap and self.wrap(self, self.dir, i, j):
            sys.exit()

        if self.rt_cwd:
            os.chdir(self.rt_cwd)
        if self.rt_env:
            os.environ.update(self.rt_env)
        for f in self.stdiop:
            os.close(f)

        os.dup2(self.rt_in,  0)
        os.dup2(self.rt_out, 1)
        os.dup2(self.rt_err, 2)

        if self.sched:
            os.sched_setaffinity(os.getpid(), self.sched)

        # no return
        os.execvp(self.rt_args[0], self.rt_args)

    def __getattr__(self, k: str) -> Any:
        return getattr(self.case, k)
