"""Windows-only bounded ConPTY QA of installed commands; never a product API."""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import subprocess
from threading import Condition, Thread
from time import monotonic

SENTINEL = "T011-FAKE-SECRET-SENTINEL"
OUTPUT_LIMIT = 2 * 1024**2
RECEIPT_LIMIT = 256 * 1024

# Generated only in an owned external QA directory. Imports observe the REAL
# product; no decisions/limits/streams/engine are replaced. Guards precede CLI.
STARTUP_GUARD = r'''
import atexit, hashlib, json, os, socket, sys
from pathlib import Path
attempts = {"network": 0, "provider": 0, "authorization": 0}
def network(*args, **kwargs):
    attempts["network"] += 1
    raise RuntimeError("T011 denies outbound network")
def provider(*args, **kwargs):
    attempts["provider"] += 1
    raise RuntimeError("T011 denies real provider construction/transport")
def authorization(*args, **kwargs):
    attempts["authorization"] += 1
    raise RuntimeError("T011 denies live authorization")
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = network
os.environ["LANGSMITH_TRACING"] = os.environ["LANGCHAIN_TRACING_V2"] = "false"
os.environ["OPENAI_LOG"] = ""
import httpx, openai
httpx.Client.__init__ = httpx.Client.send = provider
openai.OpenAI.__init__ = provider
import techlens, techlens.authorization as auth, techlens.application as app
auth.authorize_live_run = authorization
from techlens.budget import BudgetLedger
ledger_constructions = 0
original_ledger_init = BudgetLedger.__init__
def ledger_init(self, *args, **kwargs):
    global ledger_constructions
    ledger_constructions += 1
    original_ledger_init(self, *args, **kwargs)
BudgetLedger.__init__ = ledger_init
sessions = []
class ObservedSession(app.AssessmentSession):
    def __init__(self, request, dependencies, **kwargs):
        self.observed_dependencies = dependencies
        self.starts = self.resumes = 0
        sessions.append(self)
        super().__init__(request, dependencies, **kwargs)
    def start(self):
        self.starts += 1
        return super().start()
    def resume(self, decision):
        self.resumes += 1
        return super().resume(decision)
app.AssessmentSession = ObservedSession
def receipt():
    import ctypes
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetStdHandle.restype = ctypes.c_void_p
    kernel.GetConsoleScreenBufferInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    screen = (ctypes.c_short * 11)()
    console_info = bool(kernel.GetConsoleScreenBufferInfo(kernel.GetStdHandle(-11), screen))
    import msvcrt
    kernel.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    input_mode = ctypes.c_ulong()
    real_console_input = bool(kernel.GetConsoleMode(msvcrt.get_osfhandle(sys.stdin.fileno()), ctypes.byref(input_mode)))
    from techlens.exporting import export_bytes
    from techlens.presentation import PublicOutput
    observations = []
    for session in sessions:
        state = session.state or {}
        deps = session.observed_dependencies
        ledger = state.get("budget_ledger")
        item = {"starts": session.starts, "resumes": session.resumes,
            "current_round": state.get("current_round"), "plan_status": state.get("plan_status"),
            "github": len(deps.github.calls), "search": deps.search.calls,
            "planner_generation": deps.planner.generation_calls,
            "analyzer_generation": deps.analyzer.generation_calls,
            "settled": str(ledger.settled) if ledger else None,
            "outstanding": str(ledger.outstanding) if ledger else None}
        if state.get("report"):
            scenario = next((value for value in ("single", "comparison", "partial")
                if value in sys.argv), "single")
            item["status"] = state["report"].status
            item["canonical_hashes"] = {name: hashlib.sha256(data).hexdigest()
                for name, data in export_bytes(state, PublicOutput(dict(os.environ)),
                    mode="demo", scenario=scenario).items()}
        observations.append(item)
    data = {"guard": socket.socket.connect is network and httpx.Client.send is provider
            and auth.authorize_live_run is authorization,
        "attempts": attempts, "stdin_isatty": sys.stdin.isatty(),
        "stdout_isatty": sys.stdout.isatty(), "python": sys.executable,
        "prefix": sys.prefix, "version": sys.version, "package": techlens.__file__,
        "console_size": list(screen[:2]) if console_info else None,
        "real_console_input": real_console_input,
        "sys_path": sys.path, "ledger_constructions": ledger_constructions,
        "sessions": observations, "forbidden_imports": [name for name in sys.modules
            if name == "tests" or name.startswith("tests.") or name == "evals"
            or name.startswith("techlens.evaluation")]}
    encoded = json.dumps(data, ensure_ascii=True)
    if len(encoded.encode()) > 256 * 1024 or "T011-FAKE-SECRET-SENTINEL" in encoded:
        raise RuntimeError("T011 receipt safety bound failed")
    with Path(os.environ["T011_RECEIPT"]).open("x", encoding="utf-8") as stream:
        stream.write(encoded)
atexit.register(receipt)
'''


def guarded_environment(directory, receipt):
    directory.mkdir()
    (directory / "sitecustomize.py").write_text(STARTUP_GUARD, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "USERPROFILE",
        "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "APPDATA", "PATHEXT"}}
    for key in ("OPENAI_API_KEY", "TAVILY_API_KEY", "GITHUB_TOKEN", "OPENAI_MODEL",
                "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        env.pop(key, None)
    env.update(PYTHONPATH=str(directory), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               T011_RECEIPT=str(receipt), OPENAI_API_KEY=SENTINEL,
               TAVILY_API_KEY=SENTINEL, GITHUB_TOKEN=SENTINEL,
               LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    return env


def sanitized(raw):
    value = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    # Narrow ConPTY text reconstruction. Windows emits changed screen cells,
    # so stripping cursor sequences loses unchanged portions of actual prompts.
    width, height = 140, 50
    rows = [[" "] * width for _ in range(height)]
    history, x, y, saved = [], 0, 0, (0, 0)
    tokens = re.findall(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)|\x1b.|[^\x1b]", value)
    def scroll(n=1):
        for _ in range(min(n, height)):
            history.append("".join(rows.pop(0)).rstrip())
            rows.append([" "] * width)
        if len(history) * width > OUTPUT_LIMIT:
            raise TerminalBlocked("Native rendered-output bound exceeded")
    for token in tokens:
        if token.startswith("\x1b["):
            code, parameters = token[-1], token[2:-1]
            if parameters.startswith("?") or code == "m":
                continue  # Mode/color changes do not change text cells.
            values = [int(v) if v.isdigit() else 0 for v in parameters.split(";")]
            n = min(values[0] or 1, width)
            if code in "Hf":
                y, x = min((values[0] or 1) - 1, height - 1), min(((values[1] if len(values) > 1 else 1) or 1) - 1, width - 1)
            elif code == "G": x = n - 1
            elif code == "d": y = min(n - 1, height - 1)
            elif code == "A": y = max(0, y - n)
            elif code == "B": y = min(height - 1, y + n)
            elif code == "C": x = min(width - 1, x + n)
            elif code == "D": x = max(0, x - n)
            elif code == "S": scroll(n)
            elif code == "K":
                first, last = (0, width) if values[0] == 2 else (0, x + 1) if values[0] == 1 else (x, width)
                rows[y][first:last] = [" "] * (last - first)
            elif code == "J":
                if values[0] == 2: rows = [[" "] * width for _ in range(height)]
                elif values[0] == 0:
                    rows[y][x:] = [" "] * (width - x)
                    for line in range(y + 1, height): rows[line] = [" "] * width
            elif code == "P": rows[y] = rows[y][:x] + rows[y][x + n:] + [" "] * min(n, width - x)
            elif code == "@": rows[y] = (rows[y][:x] + [" "] * n + rows[y][x:])[:width]
            elif code == "X": rows[y][x:min(width, x + n)] = [" "] * min(n, width - x)
            elif code == "s": saved = x, y
            elif code == "u": x, y = saved
        elif token.startswith("\x1b"):
            if token == "\x1b7": saved = x, y
            elif token == "\x1b8": x, y = saved
        elif token == "\r": x = 0
        elif token == "\n":
            y += 1
            if y == height: scroll(); y = height - 1
        elif token == "\b": x = max(0, x - 1)
        elif token == "\t": x = min(width - 1, (x // 8 + 1) * 8)
        elif ord(token) >= 32:
            if x == width:
                x, y = 0, y + 1
                if y == height: scroll(); y = height - 1
            rows[y][x] = token
            x += 1
    value = "\n".join(history + ["".join(row).rstrip() for row in rows]).rstrip()
    value = value.replace(SENTINEL, "[REDACTED FAKE SENTINEL]")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    return value


class TerminalBlocked(RuntimeError):
    pass


class NativeTerminal:
    """One test-owned process; 20s prompts, finite exit and 2MiB capture."""
    def __init__(self, command, cwd, env):
        if os.name != "nt":
            raise TerminalBlocked("Windows ConPTY is unavailable")
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.handles = []
        self.pc = w.HANDLE()
        self.process = None
        self.attributes = None
        self.attributes_initialized = False
        self.reader = None
        self.data = bytearray()
        self.condition = Condition()
        self.overflow = False
        self.closed = False
        self.started = monotonic()
        self.job = None
        class Coord(ctypes.Structure):
            _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short)]
        class Startup(ctypes.Structure):
            _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
                ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD), ("xsize", w.DWORD),
                ("ysize", w.DWORD), ("xchars", w.DWORD), ("ychars", w.DWORD),
                ("fill", w.DWORD), ("flags", w.DWORD), ("show", w.WORD),
                ("reserved_size", w.WORD), ("reserved_bytes", ctypes.c_void_p),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]
        class StartupEx(ctypes.Structure):
            _fields_ = [("startup", Startup), ("attributes", ctypes.c_void_p)]
        class ProcessInfo(ctypes.Structure):
            _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]
        class JobBasic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
                ("active", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class JobLimits(ctypes.Structure):
            _fields_ = [("basic", JobBasic), ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        signatures = {
            "CreatePipe": ([ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), ctypes.c_void_p, w.DWORD], w.BOOL),
            "ReadFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
            "WriteFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            # COORD is a packed four-byte value in the Windows x64 ABI.
            "CreatePseudoConsole": ([w.DWORD, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], ctypes.c_long),
            "ClosePseudoConsole": ([w.HANDLE], None),
            "InitializeProcThreadAttributeList": ([ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
            "UpdateProcThreadAttribute": ([ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], w.BOOL),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, w.BOOL, w.DWORD, ctypes.c_void_p, w.LPCWSTR, ctypes.POINTER(StartupEx), ctypes.POINTER(ProcessInfo)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "ResumeThread": ([w.HANDLE], w.DWORD),
        }
        try:
            for name, (args, result) in signatures.items():
                fn = getattr(self.k, name)
                fn.argtypes, fn.restype = args, result
            in_read, self.in_write = self._pipe()
            self.out_read, out_write = self._pipe()
            self.reader = Thread(target=self._read, daemon=True)
            self.reader.start()  # Drain output even during partial construction/cleanup.
            if self.k.CreatePseudoConsole(140 | (50 << 16), in_read, out_write, 0, ctypes.byref(self.pc)) != 0:
                raise TerminalBlocked("ConPTY construction was denied")
            size = ctypes.c_size_t()
            self.k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            self.attributes = ctypes.create_string_buffer(size.value)
            initialized = self.k.InitializeProcThreadAttributeList(self.attributes, 1, 0, ctypes.byref(size))
            self.attributes_initialized = bool(initialized)
            self._check(initialized)
            self._check(self.k.UpdateProcThreadAttribute(self.attributes, 0, 0x20016, self.pc,
                                                       ctypes.sizeof(w.HANDLE), None, None))
            startup, info = StartupEx(), ProcessInfo()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x100  # Do not inherit pytest's redirected STD handles.
            startup.attributes = ctypes.cast(self.attributes, ctypes.c_void_p)
            # Windows children can otherwise inherit pytest's redirected CRT handles.
            # cmd's standard CONIN$/CONOUT$ redirection binds REAL console devices;
            # no Python streams or isatty are replaced. The actual CLI follows it.
            if any(any(c in arg for c in "\r\n&|<>^") for arg in command):
                raise TerminalBlocked("Unexpected native command syntax")
            shell = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/cmd.exe")
            line = subprocess.list2cmdline([shell, "/d", "/s", "/c"]) + ' "chcp 65001 >nul & ' + subprocess.list2cmdline(command) + ' <CONIN$ >CONOUT$ 2>CONOUT$"'
            environment = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value
                in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
            job = self.k.CreateJobObjectW(None, None)
            self._check(job)
            self.job = job
            self.handles.append(job)
            limits = JobLimits()
            limits.basic.flags = 0x2000  # Kill-on-close for ONLY this test-owned tree.
            self._check(self.k.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            self._check(self.k.CreateProcessW(shell, ctypes.create_unicode_buffer(line),
                None, None, False, 0x80000 | 0x400 | 0x4, environment, str(cwd), ctypes.byref(startup), ctypes.byref(info)))
            self.process = info.process
            self.handles.extend([info.process, info.thread])
            self.pid = info.pid
            self._check(self.k.AssignProcessToJobObject(job, info.process))
            self._check(self.k.ResumeThread(info.thread) != 0xFFFFFFFF)
            for handle in (in_read, out_write):
                self.k.CloseHandle(handle)
                self.handles.remove(handle)
        except BaseException as error:
            self.close()
            if not isinstance(error, (AttributeError, OSError, TerminalBlocked)):
                raise  # Preserve cancellation after closing all owned resources.
            raise TerminalBlocked("Native terminal construction unavailable") from None

    def _check(self, result):
        if not result:
            raise TerminalBlocked("Native terminal operation denied")

    def _pipe(self):
        read, write = w.HANDLE(), w.HANDLE()
        self._check(self.k.CreatePipe(ctypes.byref(read), ctypes.byref(write), None, 0))
        self.handles.extend([read, write])
        return read, write

    def _read(self):
        buf, count = ctypes.create_string_buffer(4096), w.DWORD()
        try:
            while self.k.ReadFile(self.out_read, buf, len(buf), ctypes.byref(count), None) and count.value:
                with self.condition:
                    if len(self.data) + count.value <= OUTPUT_LIMIT:
                        self.data.extend(buf.raw[:count.value])
                    else:
                        self.overflow = True  # Continue draining; never accumulate unbounded data.
                    self.condition.notify_all()
        except OSError:
            # A closed owned pipe during bounded teardown is not a product error.
            with self.condition:
                if not self.closed:
                    self.overflow = True
                self.condition.notify_all()

    def expect(self, prompt, timeout=20):
        deadline = min(monotonic() + timeout, self.started + 120)
        with self.condition:
            # ConPTY emits incremental screen updates; cursor moves replace spaces.
            while re.sub(r"\s+", "", prompt) not in re.sub(r"\s+", "", sanitized(bytes(self.data))):
                if self.overflow or monotonic() >= deadline:
                    raise TerminalBlocked("Native prompt/output bound exceeded")
                self.condition.wait(max(0, deadline - monotonic()))

    def write(self, value):
        raw = value.encode("utf-8")
        if len(raw) > 8192:
            raise TerminalBlocked("Native input bound exceeded")
        count = w.DWORD()
        self._check(self.k.WriteFile(self.in_write, raw, len(raw), ctypes.byref(count), None))
        self._check(count.value == len(raw))

    def finish(self, timeout=30):
        if self.k.WaitForSingleObject(self.process, int(timeout * 1000)) != 0:
            raise TerminalBlocked("Native process deadline exceeded")
        code = w.DWORD()
        self._check(self.k.GetExitCodeProcess(self.process, ctypes.byref(code)))
        if self.overflow:
            raise TerminalBlocked("Native output bound exceeded")
        self.close()
        return code.value, sanitized(bytes(self.data))

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process and self.k.WaitForSingleObject(self.process, 0) == 258:
            self.k.TerminateProcess(self.process, 32767)  # Only the exact owned handle.
            self.k.WaitForSingleObject(self.process, 5000)
        if self.job:
            self.k.CloseHandle(self.job)  # Close the owned tree BEFORE the pseudoconsole.
            self.handles.remove(self.job)
            self.job = None
        if self.pc.value:
            self.k.ClosePseudoConsole(self.pc)
        if self.attributes_initialized:
            self.k.DeleteProcThreadAttributeList(self.attributes)
        if self.reader:
            self.reader.join(2)
        for handle in reversed(self.handles):
            self.k.CloseHandle(handle)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def checked_receipt(path, python):
    if not path.exists() or path.stat().st_size > RECEIPT_LIMIT:
        raise TerminalBlocked("Native guard receipt unavailable")
    text = path.read_text(encoding="utf-8")
    assert SENTINEL not in text
    value = json.loads(text)
    assert value["guard"] and not value["forbidden_imports"]
    assert value["attempts"] == {"network": 0, "provider": 0, "authorization": 0}
    assert value["stdin_isatty"] and value["stdout_isatty"]
    assert value["real_console_input"]
    prefix = Path(python).resolve().parent.parent
    assert Path(value["prefix"]).resolve() == prefix
    assert Path(value["package"]).resolve().is_relative_to(prefix)
    assert Path(value["python"]).resolve() == Path(python).resolve()
    return value
