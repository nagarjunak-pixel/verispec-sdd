# Security Policy

## Architectural Security Guarantees

1. **Zero-`eval()` Guard & Invariant AST Engine:** `verispec.compiler.expr` parses contract predicates with `ast.parse(..., mode="eval")` and enforces an explicit AST node whitelist (`Compare`, `BoolOp`, `BinOp`, `UnaryOp`, `Subscript`, constants, and pure scalar helpers `len`/`min`/`max`/`abs`), rejecting dunder identifiers (`__*`), attribute traversal, comprehensions, lambdas, and arbitrary function calls.
2. **Bounds-Checked C++20 CSR Automaton Kernels:** `csrc/verispec_kernels.cpp` validates CSR row pointers, state bounds, and trace indices before stepping transitions, with iterative (non-recursive) Tarjan SCC traversal to prevent call-stack exhaustion on deep state graphs.

## Reporting a Vulnerability

Please report security issues privately to `writetonagarjuna@gmail.com`.
