"""Native C++20 ctypes bridge (`clang++/g++ -O3 -std=c++20`) and pure-Python reference twins."""

from __future__ import annotations

import array
import ctypes
import hashlib
import shutil
import subprocess
import tempfile
import time
from collections import deque
from pathlib import Path


def _i32_buf(values: list[int]) -> tuple[array.ArrayType[int], ctypes._Pointer[ctypes.c_int32]]:
    arr = array.array("i", values if values else [0])
    addr, _ = arr.buffer_info()
    ptr = ctypes.cast(ctypes.c_void_p(addr), ctypes.POINTER(ctypes.c_int32))
    return arr, ptr


def _compile_native_lib() -> tuple[ctypes.CDLL | None, str]:
    src_path = Path(__file__).resolve().parents[3] / "csrc" / "verispec_kernels.cpp"
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not src_path.exists() or compiler is None:
        return None, "python-fallback"

    digest = hashlib.sha256(src_path.read_bytes()).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / "verispec_native_cache"
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = ".dylib" if Path("/System/Library").exists() else ".so"
    lib_path = out_dir / f"libverispec_kernels_{digest}{ext}"

    if not lib_path.exists():
        cmd = [
            compiler,
            "-O3",
            "-std=c++20",
            "-Wall",
            "-Wextra",
            "-shared",
            "-fPIC",
            str(src_path),
            "-o",
            str(lib_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None, "python-fallback"

    lib = ctypes.CDLL(str(lib_path))
    i32p = ctypes.POINTER(ctypes.c_int32)

    lib.verispec_forward_reachability.argtypes = [ctypes.c_int32, i32p, i32p, ctypes.c_int32, i32p]
    lib.verispec_forward_reachability.restype = ctypes.c_int32

    lib.verispec_liveness_co_reachability.argtypes = [ctypes.c_int32, i32p, i32p, i32p, i32p]
    lib.verispec_liveness_co_reachability.restype = ctypes.c_int32

    lib.verispec_tarjan_scc_cycles.argtypes = [ctypes.c_int32, i32p, i32p, i32p, i32p]
    lib.verispec_tarjan_scc_cycles.restype = ctypes.c_int32

    lib.verispec_verify_event_trace.argtypes = [
        ctypes.c_int32,
        ctypes.c_int32,
        i32p,
        i32p,
        i32p,
        ctypes.c_int32,
        ctypes.c_int32,
        i32p,
        i32p,
        ctypes.c_int32,
        ctypes.c_int32,
        i32p,
        i32p,
    ]
    lib.verispec_verify_event_trace.restype = ctypes.c_int32

    return lib, f"c++20-native ({Path(compiler).name} -O3)"


_NATIVE_LIB, _BACKEND_NAME = _compile_native_lib()


def backend_name() -> str:
    """Return active verification kernel backend identifier."""
    return _BACKEND_NAME


def edges_to_csr(num_states: int, edges: list[tuple[int, int]]) -> tuple[list[int], list[int]]:
    """Convert directed `(src, dst)` edges into Compressed Sparse Row arrays."""
    adj: list[list[int]] = [[] for _ in range(num_states)]
    for u, v in edges:
        if 0 <= u < num_states and 0 <= v < num_states:
            adj[u].append(v)
    row_ptr: list[int] = [0]
    col_idx: list[int] = []
    for neighbors in adj:
        col_idx.extend(neighbors)
        row_ptr.append(len(col_idx))
    return row_ptr, col_idx


def _forward_reachability_python(
    num_states: int, row_ptr: list[int], col_idx: list[int], initial_state: int
) -> list[int]:
    dist = [-1] * num_states
    dist[initial_state] = 0
    q: deque[int] = deque([initial_state])
    while q:
        u = q.popleft()
        nd = dist[u] + 1
        for pos in range(row_ptr[u], row_ptr[u + 1]):
            v = col_idx[pos]
            if dist[v] == -1:
                dist[v] = nd
                q.append(v)
    return dist


def forward_reachability(
    num_states: int,
    edges: list[tuple[int, int]],
    initial_state: int = 0,
    *,
    use_native: bool = True,
) -> list[int]:
    """Compute shortest forward hop count from `initial_state` (`-1` if unreachable)."""
    if num_states <= 0:
        return []
    if initial_state < 0 or initial_state >= num_states:
        raise ValueError(f"initial_state {initial_state} out of range 0..{num_states - 1}")
    row_ptr, col_idx = edges_to_csr(num_states, edges)
    if use_native and _NATIVE_LIB is not None:
        _rp_b, rp = _i32_buf(row_ptr)
        _ci_b, ci = _i32_buf(col_idx)
        out_arr = array.array("i", [0] * num_states)
        out_addr, _ = out_arr.buffer_info()
        out = ctypes.cast(ctypes.c_void_p(out_addr), ctypes.POINTER(ctypes.c_int32))
        rc = _NATIVE_LIB.verispec_forward_reachability(num_states, rp, ci, initial_state, out)
        if rc >= 0:
            return out_arr.tolist()
    return _forward_reachability_python(num_states, row_ptr, col_idx, initial_state)


def _liveness_co_reachability_python(
    num_states: int, row_ptr: list[int], col_idx: list[int], terminal_mask: list[int]
) -> list[int]:
    rev_adj: list[list[int]] = [[] for _ in range(num_states)]
    for u in range(num_states):
        for pos in range(row_ptr[u], row_ptr[u + 1]):
            rev_adj[col_idx[pos]].append(u)
    dist = [-1] * num_states
    q: deque[int] = deque()
    for s in range(num_states):
        if terminal_mask[s] != 0:
            dist[s] = 0
            q.append(s)
    while q:
        u = q.popleft()
        nd = dist[u] + 1
        for prev in rev_adj[u]:
            if dist[prev] == -1:
                dist[prev] = nd
                q.append(prev)
    return dist


def liveness_co_reachability(
    num_states: int,
    edges: list[tuple[int, int]],
    terminal_states: set[int] | list[int],
    *,
    use_native: bool = True,
) -> list[int]:
    """Compute shortest hop distance from each state to ANY terminal state (`-1` if trapped)."""
    if num_states <= 0:
        return []
    term_set = set(terminal_states)
    mask = [1 if s in term_set else 0 for s in range(num_states)]
    row_ptr, col_idx = edges_to_csr(num_states, edges)
    if use_native and _NATIVE_LIB is not None:
        _rp_b, rp = _i32_buf(row_ptr)
        _ci_b, ci = _i32_buf(col_idx)
        _tm_b, tm = _i32_buf(mask)
        out_arr = array.array("i", [0] * num_states)
        out_addr, _ = out_arr.buffer_info()
        out = ctypes.cast(ctypes.c_void_p(out_addr), ctypes.POINTER(ctypes.c_int32))
        rc = _NATIVE_LIB.verispec_liveness_co_reachability(num_states, rp, ci, tm, out)
        if rc >= 0:
            return out_arr.tolist()
    return _liveness_co_reachability_python(num_states, row_ptr, col_idx, mask)


def _tarjan_scc_cycles_python(
    num_states: int, row_ptr: list[int], col_idx: list[int]
) -> tuple[list[int], list[bool]]:
    index = [-1] * num_states
    lowlink = [0] * num_states
    on_stack = [False] * num_states
    self_loop = [False] * num_states
    for u in range(num_states):
        for pos in range(row_ptr[u], row_ptr[u + 1]):
            if col_idx[pos] == u:
                self_loop[u] = True
    t_stack: list[int] = []
    scc_id = [-1] * num_states
    in_cycle = [False] * num_states
    cur_idx = 0
    scc_count = 0

    for start in range(num_states):
        if index[start] != -1:
            continue
        index[start] = cur_idx
        lowlink[start] = cur_idx
        cur_idx += 1
        on_stack[start] = True
        t_stack.append(start)
        call_stack: list[list[int]] = [[start, row_ptr[start]]]

        while call_stack:
            u, pos = call_stack[-1]
            if pos < row_ptr[u + 1]:
                v = col_idx[pos]
                call_stack[-1][1] = pos + 1
                if index[v] == -1:
                    index[v] = cur_idx
                    lowlink[v] = cur_idx
                    cur_idx += 1
                    on_stack[v] = True
                    t_stack.append(v)
                    call_stack.append([v, row_ptr[v]])
                elif on_stack[v] and index[v] < lowlink[u]:
                    lowlink[u] = index[v]
            else:
                if lowlink[u] == index[u]:
                    members: list[int] = []
                    while True:
                        w = t_stack.pop()
                        on_stack[w] = False
                        scc_id[w] = scc_count
                        members.append(w)
                        if w == u:
                            break
                    cyc = len(members) >= 2 or (len(members) == 1 and self_loop[members[0]])
                    if cyc:
                        for w in members:
                            in_cycle[w] = True
                    scc_count += 1
                call_stack.pop()
                if call_stack:
                    parent = call_stack[-1][0]
                    if lowlink[u] < lowlink[parent]:
                        lowlink[parent] = lowlink[u]
    return scc_id, in_cycle


def tarjan_scc_cycles(
    num_states: int,
    edges: list[tuple[int, int]],
    *,
    use_native: bool = True,
) -> tuple[list[int], list[bool]]:
    """Compute `(scc_ids, in_directed_cycle)` for each state in `0..num_states-1`."""
    if num_states <= 0:
        return [], []
    row_ptr, col_idx = edges_to_csr(num_states, edges)
    if use_native and _NATIVE_LIB is not None:
        _rp_b, rp = _i32_buf(row_ptr)
        _ci_b, ci = _i32_buf(col_idx)
        out_scc_arr = array.array("i", [0] * num_states)
        out_cyc_arr = array.array("i", [0] * num_states)
        scc_addr, _ = out_scc_arr.buffer_info()
        cyc_addr, _ = out_cyc_arr.buffer_info()
        out_scc = ctypes.cast(ctypes.c_void_p(scc_addr), ctypes.POINTER(ctypes.c_int32))
        out_cyc = ctypes.cast(ctypes.c_void_p(cyc_addr), ctypes.POINTER(ctypes.c_int32))
        rc = _NATIVE_LIB.verispec_tarjan_scc_cycles(num_states, rp, ci, out_scc, out_cyc)
        if rc >= 0:
            return out_scc_arr.tolist(), [bool(v) for v in out_cyc_arr.tolist()]
    return _tarjan_scc_cycles_python(num_states, row_ptr, col_idx)


def _verify_event_trace_python(
    num_states: int,
    num_events: int,
    transitions: list[tuple[int, int, int]],
    initial_state: int,
    terminal_mask: list[int],
    trace_events: list[int],
    require_terminal: bool,
) -> tuple[int, int, int]:
    table: dict[tuple[int, int], int] = {}
    for s, e, d in transitions:
        if 0 <= s < num_states and 0 <= e < num_events and 0 <= d < num_states:
            table.setdefault((s, e), d)
    cur = initial_state
    for step, evt in enumerate(trace_events):
        if evt < 0 or evt >= num_events or (cur, evt) not in table:
            return 1, cur, step
        cur = table[(cur, evt)]
    if require_terminal and terminal_mask[cur] == 0:
        return 2, cur, len(trace_events)
    return 0, cur, -1


def verify_event_trace_kernel(
    num_states: int,
    num_events: int,
    transitions: list[tuple[int, int, int]],
    initial_state: int,
    terminal_states: set[int] | list[int],
    trace_events: list[int],
    *,
    require_terminal: bool = False,
    use_native: bool = True,
) -> tuple[int, int, int]:
    """Step event trace returning `(status_code, final_state, failed_step)`."""
    if num_states <= 0 or initial_state < 0 or initial_state >= num_states or num_events < 0:
        raise ValueError("Invalid automaton dimensions or initial_state")
    term_set = set(terminal_states)
    mask = [1 if s in term_set else 0 for s in range(num_states)]
    if use_native and _NATIVE_LIB is not None:
        m = len(transitions)
        _src_b, src_a = _i32_buf([t[0] for t in transitions])
        _evt_b, evt_a = _i32_buf([t[1] for t in transitions])
        _dst_b, dst_a = _i32_buf([t[2] for t in transitions])
        _tm_b, tm_a = _i32_buf(mask)
        _tr_b, tr_a = _i32_buf(trace_events)
        out_final = ctypes.c_int32(0)
        out_step = ctypes.c_int32(-1)
        rc = _NATIVE_LIB.verispec_verify_event_trace(
            num_states,
            num_events,
            src_a,
            evt_a,
            dst_a,
            m,
            initial_state,
            tm_a,
            tr_a,
            len(trace_events),
            1 if require_terminal else 0,
            ctypes.byref(out_final),
            ctypes.byref(out_step),
        )
        if rc >= 0:
            return int(rc), int(out_final.value), int(out_step.value)
    return _verify_event_trace_python(
        num_states, num_events, transitions, initial_state, mask, trace_events, require_terminal
    )


def benchmark_kernels(
    num_states: int = 2000, trace_len: int = 25000
) -> dict[str, float | int | str]:
    """Benchmark native C++20 automaton prover & trace stepper vs pure-Python fallback."""
    num_events = 8
    edges: list[tuple[int, int]] = []
    transitions: list[tuple[int, int, int]] = []
    for s in range(num_states - 1):
        for e in range(4):
            dst = min(num_states - 1, s + 1 + (e * 3))
            edges.append((s, dst))
            transitions.append((s, e, dst))
    row_ptr, col_idx = edges_to_csr(num_states, edges)
    mask = [1 if s == num_states - 1 else 0 for s in range(num_states)]
    trace = [i % 4 for i in range(trace_len)]

    if _NATIVE_LIB is not None:
        _rp_b, rp = _i32_buf(row_ptr)
        _ci_b, ci = _i32_buf(col_idx)
        _tm_b, tm = _i32_buf(mask)
        _src_b, src_a = _i32_buf([t[0] for t in transitions])
        _evt_b, evt_a = _i32_buf([t[1] for t in transitions])
        _dst_b, dst_a = _i32_buf([t[2] for t in transitions])
        _tr_b, tr_a = _i32_buf(trace)
        out1_b = array.array("i", [0] * num_states)
        out2_b = array.array("i", [0] * num_states)
        out1 = ctypes.cast(ctypes.c_void_p(out1_b.buffer_info()[0]), ctypes.POINTER(ctypes.c_int32))
        out2 = ctypes.cast(ctypes.c_void_p(out2_b.buffer_info()[0]), ctypes.POINTER(ctypes.c_int32))
        out_f = ctypes.c_int32(0)
        out_s = ctypes.c_int32(-1)

        t0 = time.perf_counter()
        for _ in range(25):
            _NATIVE_LIB.verispec_forward_reachability(num_states, rp, ci, 0, out1)
            _NATIVE_LIB.verispec_liveness_co_reachability(num_states, rp, ci, tm, out1)
            _NATIVE_LIB.verispec_tarjan_scc_cycles(num_states, rp, ci, out1, out2)
            _NATIVE_LIB.verispec_verify_event_trace(
                num_states,
                num_events,
                src_a,
                evt_a,
                dst_a,
                len(transitions),
                0,
                tm,
                tr_a,
                len(trace),
                1,
                ctypes.byref(out_f),
                ctypes.byref(out_s),
            )
        native_ms = ((time.perf_counter() - t0) * 1000.0) / 25.0
    else:  # pragma: no cover
        native_ms = 0.0

    t1 = time.perf_counter()
    for _ in range(25):
        _forward_reachability_python(num_states, row_ptr, col_idx, 0)
        _liveness_co_reachability_python(num_states, row_ptr, col_idx, mask)
        _tarjan_scc_cycles_python(num_states, row_ptr, col_idx)
        _verify_event_trace_python(num_states, num_events, transitions, 0, mask, trace, True)
    python_ms = ((time.perf_counter() - t1) * 1000.0) / 25.0

    return {
        "backend": _BACKEND_NAME,
        "num_states": num_states,
        "num_transitions": len(transitions),
        "trace_events": trace_len,
        "native_ms": round(native_ms, 3),
        "python_ms": round(python_ms, 3),
        "speedup_x": round(python_ms / max(native_ms, 1e-6), 2),
    }
