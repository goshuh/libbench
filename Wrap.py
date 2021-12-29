from __future__ import annotations
from   typing   import Any

import os
import re
import time
import signal

from . import Item


__all__ = ['Wrap', 'STrace', 'MTrace', 'Perf', 'NVProf', 'WSS', 'fmt_perf_tlb', 'fmt_wss']


def float_div(a: float, b: float) -> float:
    try:
        return a / b
    except ZeroDivisionError:
        return 0.0


def fmt_perf_ldc(i: int, cs: str) -> list[float]:
    sp = list(map(float, cs.split()))
    sp.append(sp[0] - sp[1]) # l1_miss

    return [float(i),
            float_div(sp[4], sp[0]),
            float_div(sp[2], sp[4]),
            float_div(sp[3], sp[2])]


def fmt_perf_tlb(i: int, cs: str) -> list[float]:
    sp = list(map(float, cs.split()))
    sp.append(sp[1] + sp[2]) # l1 tlb miss

    return [float(i),
            sp[0],
            float_div(sp[4], sp[0]),
            float_div(sp[2], sp[4]),
            sp[3]]


def fmt_wss(i: int, cs: str) -> list[float]:
    sp = list(map(float, cs.split()))

    return [float(i),
            sp[0] / 1024,
            sp[1] / 1024,
            sp[2] / 1024]


class Wrap(object):

    def __init__(self, *a: str, **kw: Any):
        pass

    def __call__(self, i: Item, d: str, m: int, n: int) -> bool:
        pass


class STrace(Wrap):

    #                   ttt        call (args    )  =   ret  <T         >
    pat = re.compile(r'(\d+\.\d+) (\w+)\(([^)]+)\) += +(\w+) <(\d+\.\d+)>')

    def __init__(self, *a: str, **kw: Any):
        super().__init__(*a, **kw)
        self.name = 'strace'
        self.evts = 'trace=%memory'
        self.__dict__.update(kw)

    def __call__(self, i: Item, d: str, m: int, n: int) -> bool:
        fn = os.path.join(d, f'{i.case}-{m}-{n}-{self.name}.log')

        # strace doesn't work with an existing pipe
        evt = ['-e', self.evts] if self.evts else []

        i.rt_args = ['strace',
                     '-T',
                     '-ttt',
                     '-o', fn] + evt + i.rt_args

        if (pid := os.fork()) == 0:
            return False

        os.waitpid(pid, 0)

        with open(fn, 'r') as fi, open(fn + '.post', 'w') as fo:
            for cs in fi:
                if not (mat := STrace.pat.match(cs)):
                    continue

                func = mat.group(2)
                args = mat.group(3).split(', ')

                # only preliminary processing
                ds = f'{mat.group(1)} {mat.group(5)} {func}'

                match func:
                    case 'brk:':
                        ds += f' {mat.group(3)} {mat.group(4)}\n'
                    case 'mmap':
                        sz  = hex(int(args[1]))
                        ds += f' {mat.group(4)} {sz}'
                        ds += f'\n' if args[4] == '-1' else f' {args[4]} {args[5]}\n'
                    case 'munmap':
                        sz  = hex(int(args[1]))
                        ds += f' {args[0]} {sz}\n'
                    case 'mprotect':
                        sz  = hex(int(args[1]))
                        ds += f' {args[0]} {sz}\n'
                    case _:
                        ds +=  '\n'

                fo.write(ds)

        # clear all
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        return True


class MTrace(Wrap):

    #                  @  bin   :  (func    )   [addr   ]  +/-    0xaddr   0xsize
    pat = re.compile(r'@ ([^:]+):(\(([^)]+)\))?\[([^]]+)] ([+-]) (0x\w+)( (0x\w+))?')

    def __init__(self, *a: str, **kw: Any):
        super().__init__(*a, **kw)
        self.name = 'mtrace'
        self.__dict__.update(kw)

    def __call__(self, i: Item, d: str, m: int, n: int) -> bool:
        fn = os.path.join(d, f'{i.case}-{m}-{n}-{self.name}.log')

        # no time information...
        i.rt_env['LD_PRELOAD'  ] = os.path.join(os.path.dirname(__file__), 'c', 'libmtrace.so')
        i.rt_env['MALLOC_TRACE'] = fn

        if (pid := os.fork()) == 0:
            return False

        os.waitpid(pid, 0)

        with open(fn, 'r') as fi, open(fn + '.post', 'w') as fo:
            for cs in fi:
                if not (mat := MTrace.pat.match(cs)):
                    continue

                # only preliminary processing
                if mat.group(5) == '+':
                    fo.write(f'+ {mat.group(6)} {mat.group(8)}\n')
                else:
                    fo.write(f'- {mat.group(6)}\n')

        # clear all
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        return True


class Perf(Wrap):

    ld_dt = ['mem_inst_retired.all_loads',
             'dtlb_load_misses.stlb_hit',
             'dtlb_load_misses.miss_causes_a_walk',
             'dtlb_load_misses.walk_pending']

    st_dt = ['mem_inst_retired.all_stores',
             'dtlb_store_misses.stlb_hit',
             'dtlb_store_misses.miss_causes_a_walk',
             'dtlb_store_misses.walk_pending']

    # all_loads = l1_hit + l1_miss + hit_lfb
    # see: https://community.intel.com/t5/Software-Tuning-Performance/Memory-load-performance-counter-on-Haswell/td-p/1046679
    ld_ch = ['mem_inst_retired.all_loads',
             'mem_load_retired.l1_hit',
             'mem_load_retired.l2_miss',
             'mem_load_retired.l3_miss']

    def __init__(self, *a: str, **kw: Any):
        super().__init__(*a, **kw)
        self.name = 'perf'
        self.freq =  100
        self.dly  =  1.0

        self.__dict__.update(kw)

        self.subs =  list(a) if a else Perf.ld_ch

    def __call__(self, i: Item, d: str, m: int, n: int) -> bool:
        if len(self.subs) > 4:
            print(f'WARNING: Perf: simultaneously enabling {self.subs} events would lead to '
                            'PMC multiplexing and scaling, reducing accuracy')

        pre = os.path.join(d, f'{i.case}-{m}-{n}-{self.name}')

        if len(self.subs):
            i.rt_args = ['perf',
                         'record',
                         '-F', str(self.freq),
                         '-o', f'{pre}.data',
                         '-e', ','.join(self.subs),
                         '--'] + i.rt_args
        else:
            print(f'WARNING: Perf: no events enabled')

        # spawn perf-record
        if (pid := os.fork()) == 0:
            return False

        os.waitpid(pid, 0)

        # spawn perf-script > self
        r, w = os.pipe()

        if (pid := os.fork()) == 0:
            args = ['perf',
                    'script',
                    '-i', f'{pre}.data',
                    '-F', '-comm,-tid,-ip']
            os.close (r)
            os.dup2  (w, 1)
            os.execvp(args[0], args)

        os.close(w)

        prv =  None
        nil = {e: 0 for e in self.subs}
        num = {e: 0 for e in self.subs}
        with os.fdopen(r, 'r') as fi, open(f'{pre}.log', 'w') as fds:
            for cs in fi:
                sp  = cs.split()
                cur = float(sp[0][:-1])
                evt =       sp[2][:-1]

                num[evt] += int(sp[1])

                if prv is None:
                    prv = cur
                if (cur - prv) >= self.dly:
                    prv = cur
                    fds.write(' '.join(map(str, num.values())) + '\n')
                    num.update(nil)

        # clear all
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        return True


class NVProf(Wrap):

    def __init__(self, *a: str, **kw: Any):
        super().__init__(*a, **kw)
        self.name = 'nvprof'
        self.__dict__.update(kw)

    def __call__(self, i: Item, d: str, m: int, n: int) -> bool:
        fn = os.path.join(d, f'{i.case}-{m}-{n}-{self.name}.log')

        i.rt_args = ['nvprof',
                     '--print-api-trace',
                     '--print-gpu-trace',
                     '--track-memory-allocations', 'on',
                     '--log-file', fn] + i.rt_args

        if (pid := os.fork()) == 0:
            return False

        os.waitpid(pid, 0)

        pos = False
        pat = re.compile(r'\S+\([^\)]+\)')

        with open(fn, 'r') as fi, open(fn + '.post', 'w') as fo:
            for cs in fi:
                if pos:
                    if cs[0] == '\n':
                        break

                    size = cs[ 86:95].strip()
                    name = cs[170:  ].strip()

                    match name:
                        case '[CUDA memcpy HtoD]':
                            fo.write(f'memcpy(x, {size}, HtoD)\n')
                        case '[CUDA memcpy DtoH]':
                            fo.write(f'memcpy(x, {size}, DtoH)\n')
                        case '[CUDA memcpy DtoD]':
                            fo.write(f'memcpy(x, {size}, DtoD)\n')
                        case '[CUDA memset]':
                            fo.write(f'memset(x, {size})\n')
                        case _ if (mat := pat.match(name)):
                            fo.write(f'{mat.group(0)}\n')

                elif cs[0] == ' ':
                    pos = True

        # clear all
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        return True


class WSS(Wrap):

    def __init__(self, *a: str, **kw: Any):
        super().__init__(*a, **kw)
        self.max  = -1
        self.dly  =  0.01
        self.mode =  1
        self.stop =  1
        self.prof =  0

        self.__dict__.update(kw)

    def __call__(self, i: Item, d: str, m: int, n: int) -> bool:
        def gen(a: int):
            t = 1
            while t < a:
                yield t
                t <<= 1

        if (pid := os.fork()) == 0:
            return False

        # stop it first
        if self.stop:
            os.kill(pid, signal.SIGSTOP)

        fn_clear_refs = os.path.join(os.sep, 'proc', str(pid), 'clear_refs')
        fn_smaps      = os.path.join(os.sep, 'proc', str(pid), 'smaps')

        # floats
        fds = {t: open(os.path.join(d, f'{i.case}-{m}-{n}-wss-{t * self.dly:.2f}.log'), 'w')
                  for t in gen(1 / self.dly if self.prof else 2)}

        cnt = 0
        dly = self.dly
        while self.max < 0 or cnt < self.max:
            cnt += 1
            dif  = dly
            rss  = 0
            pss  = 0
            ref  = 0

            try:
                # clear all the access bits of the child
                with open(fn_clear_refs, 'w') as fd:
                    fd.write(str(self.mode))
            except PermissionError:
                break

            try:
                if self.stop:
                    os.kill(pid, signal.SIGCONT)
                    dif = time.time()
                time.sleep(dly)
                if self.stop:
                    os.kill(pid, signal.SIGSTOP)
                    dif = time.time() - dif
            except ProcessLookupError:
                break

            try:
                # read rss/pss/ref
                with open(fn_smaps) as fd:
                    for cs in fd:
                        if   cs.startswith('Rss:'):
                            rss += int(cs.split()[1])
                        elif cs.startswith('Pss:'):
                            pss += int(cs.split()[1])
                        elif cs.startswith('Referenced:'):
                            ref += int(cs.split()[1])
            except PermissionError:
                break

            # caveat: the time or performance is not accurate
            #   kernel's pte traversal definitely evict application's working set in the cache hierarchies,
            #   and the tlb entries are also flushed so that application experiences more ptw
            #   the reduced performance also makes the number of referenced pages smaller
            fds[round(dif / self.dly)].write(f'{rss} {pss} {ref}\n')

            # next iteration
            if self.prof:
                dly *= 2
            if dly > 1 or not self.prof:
                dly  = self.dly

        # clear all
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        for f in fds.values():
            f.close()

        return True
