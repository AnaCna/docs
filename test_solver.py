from ortools.sat.python import cp_model
from datetime import datetime, timedelta
from collections import defaultdict, deque

TICKS_PER_DAY = 20
PAVE_TICKS_PER_M = 20
WALK_TICKS_PER_M = 1

COST_SCALE = 20
WALK_COST_PER_M_S = 10 * COST_SCALE
UTURN_COST_S = 60 * COST_SCALE
WAIT_COST_PER_TICK = 50
LATE_COST_PER_TICK = 100
PAVE_COST_PER_M_S = 100 * COST_SCALE

BIG = 2_000_000_000

def dt_to_ticks(base_dt: datetime, dt: datetime) -> int:
    return int(round((dt - base_dt).total_seconds() / 86400.0 * TICKS_PER_DAY))

def ticks_to_dt(base_dt: datetime, ticks: int) -> datetime:
    return base_dt + timedelta(days=ticks / TICKS_PER_DAY)



def build_test_data():
    today0 = datetime(2025, 10, 16, 0, 0, 0)
    default_release_dt = datetime(2025, 10, 10, 0, 0, 0)

    nodes = ["红头", "红尾", "联通点", "黑尾"]
    idx = {n: i for i, n in enumerate(nodes)}
    start_node = idx["红尾"]
    end_candidates = [idx["红尾"], idx["联通点"], idx["黑尾"], idx["红头"]]

    undirected_edges = [
        (idx["红头"],   idx["联通点"], 50),
        (idx["联通点"], idx["红尾"],   60),
        (idx["联通点"], idx["黑尾"],   70),
    ]
    edge_names = {0: "红头-联通点", 1: "联通点-红尾", 2: "联通点-黑尾"}

    releases_by_edge = {}
    for e_id, (u, v, Lb) in enumerate(undirected_edges):
        rel = []
        for k in range(Lb):
            dt = default_release_dt
            if e_id == 0 and k >= int(2 * Lb / 3):
                dt = datetime(2025, 10, 18, 0, 0, 0)
            if e_id == 1 and k >= int(2 * Lb / 3):
                dt = datetime(2025, 10, 17, 0, 0, 0)
            if e_id == 2:
                if 10 <= k < 20: dt = datetime(2025, 10, 19, 0, 0, 0)
                if k >= 50:      dt = datetime(2025, 10, 21, 0, 0, 0)
            rel.append(dt_to_ticks(today0, dt))
        releases_by_edge[e_id] = rel

    due_dt = {
        0: datetime(2028, 1, 1, 0, 0, 0),
        1: datetime(2027, 6, 1, 0, 0, 0),
        2: datetime(2028, 6, 1, 0, 0, 0),
    }
    due_tick = {e: dt_to_ticks(today0, due_dt[e]) for e in due_dt}

    adj_edges_by_node = defaultdict(list)
    for e_id, (u, v, Lb) in enumerate(undirected_edges):
        adj_edges_by_node[u].append(e_id)
        adj_edges_by_node[v].append(e_id)

    return {
        "today0": today0,
        "nodes": nodes, "idx": idx,
        "start_node": start_node,
        "end_candidates": end_candidates,
        "undirected_edges": undirected_edges,
        "edge_names": edge_names,
        "releases_by_edge": releases_by_edge,
        "due_tick": due_tick,
        "adj_edges_by_node": adj_edges_by_node,
    }


def merge_blocks_by_day(data):
    undirected_edges = data["undirected_edges"]
    releases = data["releases_by_edge"]

    merged = {}
    for e_id, (u, v, Lb) in enumerate(undirected_edges):
        rel = releases[e_id]
        out = []
        if Lb == 0:
            merged[e_id] = out
            continue
        cur_start = 0
        cur_bucket = rel[0] // TICKS_PER_DAY
        for k in range(1, Lb):
            b = rel[k] // TICKS_PER_DAY
            if b != cur_bucket:
                cnt = k - cur_start
                out.append({
                    "blk_id": len(out),
                    "start_k": cur_start,
                    "end_k": k - 1,
                    "len_m": cnt * 10,
                    "release_tick": cur_bucket * TICKS_PER_DAY,
                })
                cur_start = k
                cur_bucket = b
        cnt = Lb - cur_start
        out.append({
            "blk_id": len(out),
            "start_k": cur_start,
            "end_k": Lb - 1,
            "len_m": cnt * 10,
            "release_tick": cur_bucket * TICKS_PER_DAY,
        })
        merged[e_id] = out
    return merged


def build_nodes_and_arcs(data, merged):
    undirected_edges = data["undirected_edges"]
    start_node = data["start_node"]
    end_cands = set(data["end_candidates"])
    adj_edges_by_node = data["adj_edges_by_node"]

    NODES = []
    svc_of = {}
    w_plus = {}
    w_minus = {}

    def add_svc(e, blk, dir_sign, start_nd=None, end_nd=None, len_m=0, release_tick=0):
        nid = len(NODES)
        NODES.append({
            "nid": nid, "kind": "svc",
            "edge": e, "blk": blk, "dir": dir_sign,
            "len_m": len_m,
            "dur": len_m * PAVE_TICKS_PER_M,
            "release": release_tick,
            "start_node": start_nd, "end_node": end_nd,
        })
        svc_of[(e, blk, dir_sign)] = nid
        return nid

    def add_walk_plus(e, blk, len_m):
        nid = len(NODES)
        NODES.append({
            "nid": nid, "kind": "walk",
            "edge": e, "blk": blk, "towards": "lower",
            "len_m": len_m,
            "dur": len_m * WALK_TICKS_PER_M,
        })
        w_plus[(e, blk)] = nid
        return nid

    def add_walk_minus(e, blk, len_m):
        nid = len(NODES)
        NODES.append({
            "nid": nid, "kind": "walk",
            "edge": e, "blk": blk, "towards": "higher",
            "len_m": len_m,
            "dur": len_m * WALK_TICKS_PER_M,
        })
        w_minus[(e, blk)] = nid
        return nid

    startable = set()
    endable = set()
    for e_id,(u,v,_) in enumerate(undirected_edges):
        blks = merged[e_id]
        B = len(blks)
        for b in blks:
            k   = b["blk_id"]
            Lm  = b["len_m"]
            rel = b["release_tick"]

            st_nd = u if k==0 else None
            ed_nd = v if k==B-1 else None
            nid_p = add_svc(e_id, k, +1, st_nd, ed_nd, Lm, rel)

            st_nd2 = v if k==B-1 else None
            ed_nd2 = u if k==0 else None
            nid_m = add_svc(e_id, k, -1, st_nd2, ed_nd2, Lm, rel)

            if st_nd == start_node:  startable.add(nid_p)
            if st_nd2 == start_node: startable.add(nid_m)
            if (ed_nd is not None) and (ed_nd in end_cands):   endable.add(nid_p)
            if (ed_nd2 is not None) and (ed_nd2 in end_cands): endable.add(nid_m)

            add_walk_plus(e_id, k, Lm)
            add_walk_minus(e_id, k, Lm)

    SUCC = defaultdict(list)

    def add_arc(u, v, first_step=False):
        SUCC[u].append((v, first_step))

    for e_id,(u,v,_) in enumerate(undirected_edges):
        blks = merged[e_id]; B=len(blks)
        for k in range(B):
            nid_p = svc_of[(e_id,k,+1)]
            nid_m = svc_of[(e_id,k,-1)]
            wp    = w_plus[(e_id,k)]
            wm    = w_minus[(e_id,k)]

            if k < B-1:
                add_arc(nid_p, svc_of[(e_id,k+1,+1)], False)
            if k > 0:
                add_arc(nid_m, svc_of[(e_id,k-1,-1)], False)

            add_arc(nid_p, wp, True)
            add_arc(nid_m, wm, True)

            if k > 0:
                add_arc(wp, w_plus[(e_id,k-1)], False)
                add_arc(wp, svc_of[(e_id,k-1,-1)], False)
            if k < B-1:
                add_arc(wm, w_minus[(e_id,k+1)], False)
                add_arc(wm, svc_of[(e_id,k+1,+1)], False)

            if k == 0:
                for e2 in adj_edges_by_node[u]:
                    if e2 == e_id:
                        continue
                    bl2 = merged[e2]
                    B2 = len(bl2)
                    add_arc(wp, svc_of[(e2,0,+1)], False)
                    add_arc(wp, svc_of[(e2,B2-1,-1)], False)
                    add_arc(wm, svc_of[(e2,0,+1)], False)
                    add_arc(wm, svc_of[(e2,B2-1,-1)], False)
            if k == B-1:
                for e2 in adj_edges_by_node[v]:
                    if e2 == e_id:
                        continue
                    bl2 = merged[e2]
                    B2 = len(bl2)
                    add_arc(wp, svc_of[(e2,0,+1)], False)
                    add_arc(wp, svc_of[(e2,B2-1,-1)], False)
                    add_arc(wm, svc_of[(e2,0,+1)], False)
                    add_arc(wm, svc_of[(e2,B2-1,-1)], False)

            def connect_service_to_adjacent(svc_nid, node_id):
                if node_id is None:
                    return
                for e2 in adj_edges_by_node[node_id]:
                    if e2 == e_id:
                        continue
                    u2, v2, _ = undirected_edges[e2]
                    bl2 = merged[e2]
                    B2 = len(bl2)
                    if node_id == u2:
                        add_arc(svc_nid, svc_of[(e2,0,+1)], True)
                    if node_id == v2:
                        add_arc(svc_nid, svc_of[(e2,B2-1,-1)], True)

            connect_service_to_adjacent(nid_p, NODES[nid_p]["end_node"])
            connect_service_to_adjacent(nid_m, NODES[nid_m]["end_node"])

    return {
        "NODES": NODES,
        "SUCC": SUCC,
        "svc_of": svc_of,
        "w_plus": w_plus,
        "w_minus": w_minus,
        "startable": startable,
        "endable": endable,
        "merged": merged,
    }


def solve_with_cuts(data, topo):
    model = cp_model.CpModel()

    NODES = topo["NODES"]; SUCC = topo["SUCC"]
    svc_of = topo["svc_of"]
    startable = topo["startable"]; endable = topo["endable"]
    merged = topo["merged"]
    due_tick = data["due_tick"]
    edge_names = data["edge_names"]
    today0 = data["today0"]

    N = len(NODES)
    zero = model.NewConstant(0)

    s = {}; wsel = {}; sel = {}
    for nd in NODES:
        nid = nd["nid"]
        if nd["kind"]=="svc":
            s[nid] = model.NewBoolVar(f"s_{nid}")
            sel[nid] = s[nid]
        else:
            wsel[nid] = model.NewBoolVar(f"w_{nid}")
            sel[nid]  = wsel[nid]

    for e_id, blks in merged.items():
        for b in blks:
            k = b["blk_id"]
            nid_p = svc_of[(e_id,k,+1)]
            nid_m = svc_of[(e_id,k,-1)]
            model.Add(s[nid_p] + s[nid_m] == 1)

    x = {}
    is_uturn_arc = {}
    for u in range(N):
        for (v, first_step) in SUCC[u]:
            var = model.NewBoolVar(f"x_{u}_{v}")
            x[(u, v)] = var
            model.Add(var <= sel[u]); model.Add(var <= sel[v])

            utu = False
            if NODES[u]["kind"]=="svc" and NODES[v]["kind"]=="svc":
                if NODES[u]["edge"]==NODES[v]["edge"] and NODES[u]["dir"] != NODES[v]["dir"]:
                    utu = True
            if NODES[u]["kind"]=="svc" and NODES[v]["kind"]=="walk" and first_step:
                utu = True
            is_uturn_arc[(u, v)] = utu

    start_flag = {nid: model.NewBoolVar(f"st_{nid}") for nid in range(N)}
    end_flag   = {nid: model.NewBoolVar(f"ed_{nid}") for nid in range(N)}
    for nid in range(N):
        if NODES[nid]["kind"]!="svc":
            model.Add(start_flag[nid]==0); model.Add(end_flag[nid]==0)
        else:
            if nid not in startable: model.Add(start_flag[nid]==0)
            if nid not in endable:   model.Add(end_flag[nid]==0)

    in_sum = {}; out_sum = {}
    for nid in range(N):
        in_vars  = [x[(u, nid)] for (u, v) in x if v == nid]
        out_vars = [x[(nid, v)] for (u, v) in x if u == nid]
        in_sum[nid]  = model.NewIntVar(0, 1, f"in_{nid}")
        out_sum[nid] = model.NewIntVar(0, 1, f"out_{nid}")
        model.Add(in_sum[nid]  == (sum(in_vars)  if in_vars  else 0))
        model.Add(out_sum[nid] == (sum(out_vars) if out_vars else 0))
        model.Add(in_sum[nid]  + start_flag[nid] == sel[nid])
        model.Add(out_sum[nid] + end_flag[nid]   == sel[nid])

    model.Add(sum(start_flag.values()) == 1)
    model.Add(sum(end_flag.values())   == 1)

    t = {nid: model.NewIntVar(0, BIG, f"t_{nid}") for nid in range(N)}
    for nd in NODES:
        if nd["kind"]=="svc":
            model.Add(t[nd["nid"]] >= nd["release"])

    for (u, v), xv in x.items():
        model.Add(t[v] >= t[u] + NODES[u]["dur"]).OnlyEnforceIf(xv)

    w = {}
    for nd in NODES:
        if nd["kind"]=="svc":
            nid = nd["nid"]
            w[nid] = model.NewIntVar(0, BIG, f"wait_{nid}")
            diff = model.NewIntVar(-BIG, BIG, f"diff_{nid}")
            model.Add(diff == t[nid] - nd["release"])
            model.AddMaxEquality(w[nid], [diff, zero])

    C_edge = {}; T_edge = {}
    for e_id, blks in merged.items():
        cands = []
        for b in blks:
            k = b["blk_id"]
            nid_p = svc_of[(e_id,k,+1)]
            nid_m = svc_of[(e_id,k,-1)]
            endp = model.NewIntVar(0, BIG, f"endp_{e_id}_{k}")
            endm = model.NewIntVar(0, BIG, f"endm_{e_id}_{k}")
            model.Add(endp == t[nid_p] + NODES[nid_p]["dur"])
            model.Add(endm == t[nid_m] + NODES[nid_m]["dur"])
            candp = model.NewIntVar(0, BIG, f"candp_{e_id}_{k}")
            candm = model.NewIntVar(0, BIG, f"candm_{e_id}_{k}")
            model.Add(candp == endp).OnlyEnforceIf(s[nid_p])
            model.Add(candp == 0).OnlyEnforceIf(s[nid_p].Not())
            model.Add(candm == endm).OnlyEnforceIf(s[nid_m])
            model.Add(candm == 0).OnlyEnforceIf(s[nid_m].Not())
            cands += [candp, candm]
        C_edge[e_id] = model.NewIntVar(0, BIG, f"C_edge_{e_id}")
        model.AddMaxEquality(C_edge[e_id], cands)

        T_edge[e_id] = model.NewIntVar(0, BIG, f"T_edge_{e_id}")
        late_diff = model.NewIntVar(-BIG, BIG, f"late_diff_{e_id}")
        model.Add(late_diff == C_edge[e_id] - due_tick[e_id])
        model.AddMaxEquality(T_edge[e_id], [late_diff, zero])

    walk_cost_terms  = [WALK_COST_PER_M_S * nd["len_m"] * wsel[nd["nid"]] for nd in NODES if nd["kind"]=="walk"]
    uturn_cost_terms = [UTURN_COST_S * x[(u, v)] for (u, v) in x if is_uturn_arc[(u, v)]]
    wait_cost_terms  = [WAIT_COST_PER_TICK * w[nid] for nid in w]
    late_cost_terms  = [LATE_COST_PER_TICK * T_edge[e] for e in T_edge]
    pave_const       = PAVE_COST_PER_M_S * sum(b["len_m"] for e in merged for b in merged[e])

    model.Minimize(sum(walk_cost_terms) + sum(uturn_cost_terms) +
                   sum(wait_cost_terms) + sum(late_cost_terms) + pave_const)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    solver.parameters.num_search_workers = 8

    added_cuts = set()
    MAX_CUT_ROUNDS = 20

    def extract():
        xv   = {(u, v): (solver.Value(x[(u, v)]) == 1) for (u, v) in x}
        selv = {nid: (solver.Value(sel[nid]) == 1) for nid in range(N)}
        start_task = None
        in_cnt = {}
        for nid in range(N):
            if not selv[nid] or NODES[nid]["kind"]!="svc": continue
            in_cnt[nid] = sum(solver.Value(x[(u, nid)]) for (u, v) in x if v == nid)
            if in_cnt[nid] == 0:
                start_task = nid
                break
        return xv, selv, start_task

    def bad_component(x_on, sel_on, start_task):
        adj = defaultdict(set)
        selected = {nid for nid,on in sel_on.items() if on}
        for (u,v),on in x_on.items():
            if on:
                adj[u].add(v); adj[v].add(u)
        if start_task is None or start_task not in selected:
            return set()
        seen = set([start_task]); dq = deque([start_task])
        while dq:
            cur = dq.popleft()
            for nb in adj[cur]:
                if nb not in seen:
                    seen.add(nb); dq.append(nb)
        bad = selected - seen
        if not bad: return set()
        comp = set(); seed = next(iter(bad)); dq = deque([seed]); comp.add(seed)
        while dq:
            cur = dq.popleft()
            for nb in adj[cur]:
                if nb in bad and nb not in comp:
                    comp.add(nb); dq.append(nb)
        return comp

    for _ in range(MAX_CUT_ROUNDS):
        res = solver.Solve(model)
        if res not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            print("无可行解/超时")
            return None
        xv, selv, st = extract()
        comp = bad_component(xv, selv, st)
        if not comp:
            break
        key = tuple(sorted(comp))
        if key in added_cuts:
            break
        border = []
        comp_set = set(comp)
        for (u, v), var in x.items():
            if (u in comp_set) ^ (v in comp_set):
                border.append(var)
        if border:
            model.Add(sum(border) >= 1)
            added_cuts.add(key)
        else:
            break

    sol = {
        "status": solver.StatusName(),
        "objective_scaled": solver.ObjectiveValue(),
        "objective_real": solver.ObjectiveValue() / COST_SCALE,
        "t": {nd["nid"]: solver.Value(t[nd["nid"]]) for nd in NODES},
        "sel": {nd["nid"]: solver.Value((s[nd["nid"]] if nd["kind"]=="svc" else wsel[nd["nid"]])) for nd in NODES},
        "x": {(u, v): solver.Value(x[(u, v)]) for (u, v) in x},
        "NODES": NODES,
        "C_edge": {e: solver.Value(C_edge[e]) for e in C_edge},
        "T_edge": {e: solver.Value(T_edge[e]) for e in T_edge},
        "edge_names": edge_names,
        "today0": today0,
    }
    return sol


def print_solution(sol):
    if sol is None: return
    NODES = sol["NODES"]; x = sol["x"]; sel = sol["sel"]; t = sol["t"]
    edge_names = sol["edge_names"]; today0 = sol["today0"]

    print("\n=== 状态 / 目标 ===")
    print(f"状态: {sol['status']}")
    print(f"目标(缩放): {sol['objective_scaled']:.0f}   目标(现实): {sol['objective_real']:.2f}")

    print("\n=== 每条大边 完工 / 逾期 ===")
    for e, C in sol["C_edge"].items():
        Te = sol["T_edge"][e]
        print(f"[{edge_names[e]}] 完工 {ticks_to_dt(today0, C)}   逾期(天) {Te/TICKS_PER_DAY:.1f}")

    succ = defaultdict(list); pred = defaultdict(list)
    for (u, v), val in x.items():
        if val == 1:
            succ[u].append(v); pred[v].append(u)

    start = None
    for nid,on in sel.items():
        if on != 1: continue
        if NODES[nid]["kind"] != "svc": continue
        if len(pred[nid]) == 0:
            start = nid; break

    route = []
    cur = start; seen=set()
    while cur is not None and cur not in seen:
        seen.add(cur); route.append(cur)
        cur = succ[cur][0] if succ[cur] else None

    print("\n=== 路线（前60步）===")
    for i, nid in enumerate(route[:60], 1):
        nd = NODES[nid]
        st = ticks_to_dt(today0, t[nid])
        ed = ticks_to_dt(today0, t[nid] + nd["dur"])
        if nd["kind"]=="svc":
            print(f"{i:03d} 服务  边{nd['edge']}[{edge_names[nd['edge']]}]  块{nd['blk']:02d}{'(+) ' if nd['dir']==1 else '(-) '}长{nd['len_m']}m  开工{st}  完工{ed}")
        else:
            print(f"{i:03d} 返走  边{nd['edge']}  块{nd['blk']:02d}  长{nd['len_m']}m  开始{st}  结束{ed}")


if __name__ == "__main__":
    data   = build_test_data()
    merged = merge_blocks_by_day(data)
    topo   = build_nodes_and_arcs(data, merged)
    sol    = solve_with_cuts(data, topo)
    print_solution(sol)
