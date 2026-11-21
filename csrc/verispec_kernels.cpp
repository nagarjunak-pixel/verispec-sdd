// Native C++20 Finite-State Automaton Reachability, Liveness Co-Reachability,
// Tarjan Livelock Cycle Detection, and Deterministic Trace Verification Kernels.
// Compiled via clang++/g++ -O3 -std=c++20 -Wall -Wextra -shared -fPIC.

#include <algorithm>
#include <cstdint>
#include <vector>

extern "C" {

// 1. Exact forward BFS shortest-hop reachability from initial_state.
// out_bfs_dist[s] = shortest hop count from initial_state, or -1 if unreachable.
// Returns count of reachable states, or -1 on invalid input.
int32_t verispec_forward_reachability(
    int32_t num_states,
    const int32_t* row_ptr,
    const int32_t* col_idx,
    int32_t initial_state,
    int32_t* out_bfs_dist
) {
    if (num_states <= 0 || row_ptr == nullptr || out_bfs_dist == nullptr) {
        return -1;
    }
    if (initial_state < 0 || initial_state >= num_states) {
        return -1;
    }

    for (int32_t i = 0; i < num_states; ++i) {
        out_bfs_dist[i] = -1;
    }

    std::vector<int32_t> queue;
    queue.reserve(static_cast<size_t>(num_states));
    out_bfs_dist[initial_state] = 0;
    queue.push_back(initial_state);

    size_t head = 0;
    while (head < queue.size()) {
        int32_t u = queue[head++];
        int32_t next_d = out_bfs_dist[u] + 1;
        int32_t start = row_ptr[u];
        int32_t end = row_ptr[u + 1];
        for (int32_t pos = start; pos < end; ++pos) {
            int32_t v = col_idx[pos];
            if (v >= 0 && v < num_states && out_bfs_dist[v] == -1) {
                out_bfs_dist[v] = next_d;
                queue.push_back(v);
            }
        }
    }

    return static_cast<int32_t>(queue.size());
}

// 2. Multi-source reverse BFS co-reachability to any terminal state.
// Builds transposed CSR in O(|S| + |E|) and computes shortest hops from each state
// to the nearest terminal state (`terminal_mask[s] != 0`).
// out_dist_to_terminal[s] = -1 iff NO terminal state is reachable from s.
int32_t verispec_liveness_co_reachability(
    int32_t num_states,
    const int32_t* row_ptr,
    const int32_t* col_idx,
    const int32_t* terminal_mask,
    int32_t* out_dist_to_terminal
) {
    if (num_states <= 0 || row_ptr == nullptr || terminal_mask == nullptr || out_dist_to_terminal == nullptr) {
        return -1;
    }

    int32_t total_edges = row_ptr[num_states];
    std::vector<int32_t> rev_deg(static_cast<size_t>(num_states), 0);
    for (int32_t u = 0; u < num_states; ++u) {
        for (int32_t pos = row_ptr[u]; pos < row_ptr[u + 1]; ++pos) {
            int32_t v = col_idx[pos];
            if (v >= 0 && v < num_states) {
                rev_deg[static_cast<size_t>(v)]++;
            }
        }
    }

    std::vector<int32_t> rev_row_ptr(static_cast<size_t>(num_states + 1), 0);
    for (int32_t i = 0; i < num_states; ++i) {
        rev_row_ptr[static_cast<size_t>(i + 1)] = rev_row_ptr[static_cast<size_t>(i)] + rev_deg[static_cast<size_t>(i)];
    }

    std::vector<int32_t> rev_col_idx(static_cast<size_t>(std::max(1, total_edges)), 0);
    std::vector<int32_t> cursor = rev_row_ptr;
    for (int32_t u = 0; u < num_states; ++u) {
        for (int32_t pos = row_ptr[u]; pos < row_ptr[u + 1]; ++pos) {
            int32_t v = col_idx[pos];
            if (v >= 0 && v < num_states) {
                int32_t slot = cursor[static_cast<size_t>(v)]++;
                rev_col_idx[static_cast<size_t>(slot)] = u;
            }
        }
    }

    std::vector<int32_t> queue;
    queue.reserve(static_cast<size_t>(num_states));
    for (int32_t s = 0; s < num_states; ++s) {
        if (terminal_mask[s] != 0) {
            out_dist_to_terminal[s] = 0;
            queue.push_back(s);
        } else {
            out_dist_to_terminal[s] = -1;
        }
    }

    size_t head = 0;
    while (head < queue.size()) {
        int32_t u = queue[head++];
        int32_t next_d = out_dist_to_terminal[u] + 1;
        for (int32_t pos = rev_row_ptr[static_cast<size_t>(u)]; pos < rev_row_ptr[static_cast<size_t>(u + 1)]; ++pos) {
            int32_t prev = rev_col_idx[static_cast<size_t>(pos)];
            if (out_dist_to_terminal[prev] == -1) {
                out_dist_to_terminal[prev] = next_d;
                queue.push_back(prev);
            }
        }
    }

    return static_cast<int32_t>(queue.size());
}

// 3. Iterative Tarjan Strongly Connected Components + directed cycle membership.
// out_in_cycle[s] = 1 iff state s belongs to an SCC of size >= 2 OR has a self-loop s -> s.
// Returns total number of SCCs, or -1 on invalid input.
int32_t verispec_tarjan_scc_cycles(
    int32_t num_states,
    const int32_t* row_ptr,
    const int32_t* col_idx,
    int32_t* out_scc_id,
    int32_t* out_in_cycle
) {
    if (num_states <= 0 || row_ptr == nullptr || out_scc_id == nullptr || out_in_cycle == nullptr) {
        return -1;
    }

    std::vector<int32_t> index(static_cast<size_t>(num_states), -1);
    std::vector<int32_t> lowlink(static_cast<size_t>(num_states), 0);
    std::vector<uint8_t> on_stack(static_cast<size_t>(num_states), 0);
    std::vector<uint8_t> has_self_loop(static_cast<size_t>(num_states), 0);
    std::vector<int32_t> tarjan_stack;
    tarjan_stack.reserve(static_cast<size_t>(num_states));

    for (int32_t u = 0; u < num_states; ++u) {
        out_scc_id[u] = -1;
        out_in_cycle[u] = 0;
        for (int32_t pos = row_ptr[u]; pos < row_ptr[u + 1]; ++pos) {
            if (col_idx[pos] == u) {
                has_self_loop[static_cast<size_t>(u)] = 1;
            }
        }
    }

    struct Frame {
        int32_t u;
        int32_t pos;
    };
    std::vector<Frame> call_stack;
    call_stack.reserve(static_cast<size_t>(num_states));

    int32_t cur_idx = 0;
    int32_t scc_count = 0;

    for (int32_t start = 0; start < num_states; ++start) {
        if (index[static_cast<size_t>(start)] != -1) {
            continue;
        }
        index[static_cast<size_t>(start)] = cur_idx;
        lowlink[static_cast<size_t>(start)] = cur_idx;
        ++cur_idx;
        on_stack[static_cast<size_t>(start)] = 1;
        tarjan_stack.push_back(start);
        call_stack.push_back({start, row_ptr[start]});

        while (!call_stack.empty()) {
            Frame& top = call_stack.back();
            int32_t u = top.u;
            if (top.pos < row_ptr[u + 1]) {
                int32_t v = col_idx[top.pos++];
                if (v < 0 || v >= num_states) {
                    continue;
                }
                if (index[static_cast<size_t>(v)] == -1) {
                    index[static_cast<size_t>(v)] = cur_idx;
                    lowlink[static_cast<size_t>(v)] = cur_idx;
                    ++cur_idx;
                    on_stack[static_cast<size_t>(v)] = 1;
                    tarjan_stack.push_back(v);
                    call_stack.push_back({v, row_ptr[v]});
                } else if (on_stack[static_cast<size_t>(v)]) {
                    if (index[static_cast<size_t>(v)] < lowlink[static_cast<size_t>(u)]) {
                        lowlink[static_cast<size_t>(u)] = index[static_cast<size_t>(v)];
                    }
                }
            } else {
                if (lowlink[static_cast<size_t>(u)] == index[static_cast<size_t>(u)]) {
                    std::vector<int32_t> members;
                    while (true) {
                        int32_t w = tarjan_stack.back();
                        tarjan_stack.pop_back();
                        on_stack[static_cast<size_t>(w)] = 0;
                        out_scc_id[w] = scc_count;
                        members.push_back(w);
                        if (w == u) {
                            break;
                        }
                    }
                    bool cyc = members.size() >= 2 ||
                               (members.size() == 1 && has_self_loop[static_cast<size_t>(members[0])] != 0);
                    if (cyc) {
                        for (int32_t w : members) {
                            out_in_cycle[w] = 1;
                        }
                    }
                    ++scc_count;
                }
                call_stack.pop_back();
                if (!call_stack.empty()) {
                    int32_t parent = call_stack.back().u;
                    if (lowlink[static_cast<size_t>(u)] < lowlink[static_cast<size_t>(parent)]) {
                        lowlink[static_cast<size_t>(parent)] = lowlink[static_cast<size_t>(u)];
                    }
                }
            }
        }
    }

    return scc_count;
}

// 4. Deterministic trace automaton stepper.
// Status codes: 0 = VALID, 1 = ILLEGAL_TRANSITION, 2 = NON_TERMINAL_END, -1 = INVALID_ARGS.
int32_t verispec_verify_event_trace(
    int32_t num_states,
    int32_t num_events,
    const int32_t* trans_src,
    const int32_t* trans_evt,
    const int32_t* trans_dst,
    int32_t num_trans,
    int32_t initial_state,
    const int32_t* terminal_mask,
    const int32_t* trace_events,
    int32_t trace_len,
    int32_t require_terminal,
    int32_t* out_final_state,
    int32_t* out_failed_step
) {
    if (num_states <= 0 || num_events < 0 || initial_state < 0 || initial_state >= num_states ||
        terminal_mask == nullptr || out_final_state == nullptr || out_failed_step == nullptr) {
        return -1;
    }

    // Build flat deterministic transition lookup table [num_states * max(1, num_events)]
    int32_t stride = std::max(1, num_events);
    std::vector<int32_t> table(static_cast<size_t>(num_states) * static_cast<size_t>(stride), -1);
    for (int32_t i = 0; i < num_trans; ++i) {
        int32_t s = trans_src[i];
        int32_t e = trans_evt[i];
        int32_t d = trans_dst[i];
        if (s >= 0 && s < num_states && e >= 0 && e < num_events && d >= 0 && d < num_states) {
            size_t idx = static_cast<size_t>(s) * static_cast<size_t>(stride) + static_cast<size_t>(e);
            if (table[idx] == -1) {
                table[idx] = d;
            }
        }
    }

    int32_t cur = initial_state;
    *out_failed_step = -1;
    for (int32_t step = 0; step < trace_len; ++step) {
        int32_t evt = trace_events[step];
        if (evt < 0 || evt >= num_events) {
            *out_final_state = cur;
            *out_failed_step = step;
            return 1;
        }
        int32_t nxt = table[static_cast<size_t>(cur) * static_cast<size_t>(stride) + static_cast<size_t>(evt)];
        if (nxt == -1) {
            *out_final_state = cur;
            *out_failed_step = step;
            return 1;
        }
        cur = nxt;
    }

    *out_final_state = cur;
    if (require_terminal != 0 && terminal_mask[cur] == 0) {
        *out_failed_step = trace_len;
        return 2;
    }
    return 0;
}

}  // extern "C"
