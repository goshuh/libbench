from __future__ import annotations
from   typing   import Any

import os
import re
import time
import signal

import bcc
import pickle

from .Item import Item


__all__ = ['Wrap', 'STrace', 'MTrace', 'Perf', 'NVProf', 'WSS', 'BPF', 'fmt_perf_tlb', 'fmt_wss']


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

        # clean up
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        self.post(fn)

        return True

    def post(self, fn: str) -> None:
        brk = 0

        with open(fn, 'r') as fi, open(fn + '.post', 'w') as fo:
            for cs in fi:
                if not (mat := STrace.pat.match(cs)):
                    continue

                utc  = mat.group(1)
                func = mat.group(2)
                args = mat.group(3).split(', ')
                ret  = mat.group(4)

                match func:
                    case 'brk':
                        new = int(ret, 16)
                        if brk and (dif := new - brk):
                            sig = '+' if dif > 0 else '-'
                            fo.write(f'{sig} {utc} {abs(dif):x}\n')
                        brk = new
                    case 'mmap':
                        fo.write(f'* {utc} {ret} {int(args[1]):x}\n')
                    case 'munmap':
                        fo.write(f'/ {utc} {args[0]} {int(args[1]):x}\n')
                    case 'mremap':
                        fo.write(f'/ {utc} {args[0]} {int(args[1]):x}\n')
                        fo.write(f'* {utc} {ret} {int(args[2]):x}\n')
                    case 'mprotect':
                        fo.write(f'= {utc} {args[0]} {int(args[1]):x}\n')


class MTrace(Wrap):

    #                   ttt        func (args    )  =  ret
    pat = re.compile(r'(\d+\.\d+) (\w+)\(([^)]+)\)( = (\w+))?')

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

        # clean up
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        self.post(fn)

        return True

    def post(self, fn: str) -> None:
        dic = {}

        with open(fn, 'r') as fi, open(fn + '.post', 'w') as fo:
            for cs in fi:
                if not (mat := MTrace.pat.match(cs)):
                    continue

                utc  = mat.group(1)
                func = mat.group(2)
                args = mat.group(3).split(', ')
                ret  = mat.group(5)

                match func:
                    case 'free':
                        if args[0] != '0':
                            fo.write(f'- {utc} {args[0]} {dic.pop(args[0])}\n')
                    case 'malloc':
                        fo.write(f'+ {utc} {ret} {args[0]}\n')
                        dic[ret] = args[0]
                    case 'calloc':
                        sz = int(args[0], 16) * int(args[1], 16)
                        fo.write(f'+ {utc} {ret} {sz:x}\n')
                        dic[ret] = f'{sz:x}'
                    case 'realloc':
                        if args[0] != '0':
                            fo.write(f'- {utc} {args[0]} {dic.pop(args[0])}\n')
                        fo.write(f'+ {utc} {ret} {args[1]}\n')
                        dic[ret] = args[1]


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

        fn = os.path.join(d, f'{i.case}-{m}-{n}-{self.name}.data')

        if len(self.subs):
            i.rt_args = ['perf',
                         'record',
                         '-F', str(self.freq),
                         '-o', fn,
                         '-e', ','.join(self.subs),
                         '--'] + i.rt_args
        else:
            print(f'WARNING: Perf: no events enabled')

        # spawn perf-record
        if (pid := os.fork()) == 0:
            return False

        os.waitpid(pid, 0)

        # clean up
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        self.post(fn)

        return True

    def post(self, fn: str) -> None:
        r, w = os.pipe()

        if (pid := os.fork()) == 0:
            args = ['perf',
                    'script',
                    '-i', fn,
                    '-F', '-comm,-tid,-ip']
            os.close (r)
            os.dup2  (w, 1)
            os.execvp(args[0], args)

        os.close(w)

        prv =  None
        nil = {e: 0 for e in self.subs}
        num = {e: 0 for e in self.subs}

        with os.fdopen(r, 'r') as fi, open(f'{fn}.post', 'w') as fds:
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

        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class NVProf(Wrap):

    pat = re.compile(r'\w+\([^\)]+\)')

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

        # clean up
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        self.post(fn)

        return True

    def post(self, fn: str) -> None:
        pos = False

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
                        case _ if (mat := NVProf.pat.match(name)):
                            fo.write(f'{mat.group(0)}\n')

                elif cs[0] == ' ':
                    pos = True


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

        # clean up
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        for f in fds.values():
            f.close()

        return True


class BPF(Wrap):

    root = os.path.dirname(__file__)

    def __init__(self, *a: str, **kw: Any):
        super().__init__(*a, **kw)
        self.name      = 'bpf'
        self.stop      =  1
        self.prog      = ''
        self.kprobe    = {}
        self.kretprobe = {}

        self.__dict__.update(kw)

        if self.prog and not os.path.isfile(self.prog):
            self.prog = os.path.join(BPF.root, self.prog)

    def __call__(self, i: Item, d: str, m: int, n: int) -> bool:
        fn = os.path.join(d, f'{i.case}-{m}-{n}-{self.name}.log')

        if not self.prog:
            print('WARNING: BPF: no program specified')
            return True

        if (pid := os.fork()) == 0:
            return False

        # stop it first
        if self.stop:
            os.kill(pid, signal.SIGSTOP);

        # signal doesn't work for the elevated process
        fr, fw = os.pipe()
        br, bw = os.pipe()

        if not os.fork():
            os.close(fw)
            os.close(br)
            os.dup2 (fr, 0)
            os.dup2 (bw, 1)

            # elevate
            os.execlp('sudo',
                      'sudo',
                      '--preserve-env=PYTHONPATH',
                       os.path.join(BPF.root, 'BPF.py'))

        os.close(fr)
        os.close(bw)

        # send self and handshake
        buf = pickle.dumps(self)

        os.write(fw, len(buf).to_bytes(4, 'little'))
        os.write(fw, buf)
        os.read (br, 1)

        if self.stop:
            os.kill(pid, signal.SIGCONT);

        os.waitpid(pid, 0);

        # clean up
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

        # stop the elevated process
        os.write(fw, b'\0')
        os.close(fw)

        # dump results
        with open(fn, 'wb') as fd:
            while True:
                if buf := os.read(br, 4096):
                    fd.write(buf)
                else:
                    break
        os.close(br)

        return True

    def priv(self) -> None:
        bpf = bcc.BPF(src_file = self.prog.encode('utf-8'))

        for k, v in self.kprobe   .items():
            bpf.attach_kprobe   (event = k, fn_name = v)
        for k, v in self.kretprobe.items():
            bpf.attach_kretprobe(event = k, fn_name = v)

        # addiitonal logic
        self.init(bpf)

        # handshake
        os.write(1, b'\0')
        os.read (0, 1)

        # output
        self.post(bpf)

    def init(self, bpf: bcc.BPF) -> None:
        pass

    def post(self, bpf: bcc.BPF) -> None:
        pass
