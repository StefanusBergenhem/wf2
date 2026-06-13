#!/usr/bin/env python3
"""cluster.py — PoC: mechanically suggest subsystem clusterings from a read-view IR.

Produces THREE candidate clusterings over the same component set, each capturing a
different kind of cohesion:

  - folder      : the directory hierarchy (the author's intended grouping)
  - depgraph    : static-import coupling (label propagation on the dependency graph)
  - gitcochange : temporal coupling (components that change together in git history)

The candidates are deterministic, derived-from-code, and stored nowhere durable.
They are the raw material an AI scout reconciles into one named subsystem map.

Usage:
  cluster.py --model model.json --repo /path/to/repo \
             --out clusters.json [--git-window 2000] [--cochange-min 4]

Consumes the spine's merged model.json (the single merge authority — nodes carry
language tags and depends_on already resolved to UIDs). Stdlib only; no network,
no LLM. Output is JSON on --out (and a summary to stderr).
"""
import argparse, json, subprocess, sys
from collections import defaultdict, Counter


def last_seg(p):
    return p.rsplit("/", 1)[-1]


# ---------- candidate 1: folder (split oversized groups deeper) ----------

def folder_clusters(nodes, big_frac=0.22):
    """Group by path prefix. Start at depth 1; recursively re-split any group that
    holds more than `big_frac` of all components by descending one more segment.
    Then merge leftover singletons that share a first segment into a "<seg>/* (misc)"
    bucket, so a flat utilities dir doesn't explode into dozens of subsystems.
    Language-agnostic: works for shallow (module-relative) and deep (web/src/...) ids."""
    total = len(nodes)

    def key_at(path, depth):
        segs = path.split("/")
        return "/".join(segs[:depth]) if len(segs) >= depth else path

    def split(uids, depth):
        groups = defaultdict(list)
        for u in uids:
            groups[key_at(nodes[u]["path"], depth)].append(u)
        out = {}
        for k, members in groups.items():
            deeper_possible = any(len(nodes[u]["path"].split("/")) > depth for u in members)
            if len(members) > big_frac * total and deeper_possible and len(members) > 2:
                out.update(split(members, depth + 1))
            else:
                out[k] = members
        return out

    groups = split(list(nodes), 1)
    out, singles_by_root = {}, defaultdict(list)
    for k, members in groups.items():
        if len(members) == 1:
            singles_by_root[nodes[members[0]]["path"].split("/")[0]].append(members[0])
        else:
            out[k] = members
    for root, uids in singles_by_root.items():
        if len(uids) >= 2:
            out[f"{root}/* (misc)"] = sorted(uids)
        else:
            out[nodes[uids[0]]["path"]] = uids
    return out


# ---------- label propagation (shared by depgraph + gitcochange) ----------

def label_propagation(node_ids, adjacency, max_iter=50):
    """Deterministic async LPA on an undirected weighted graph.
    adjacency: uid -> Counter(neighbor_uid -> weight). Isolated nodes keep own label."""
    label = {u: u for u in node_ids}
    order = sorted(node_ids)
    for _ in range(max_iter):
        changed = False
        for u in order:
            nbrs = adjacency.get(u)
            if not nbrs:
                continue
            weight = Counter()
            for v, w in nbrs.items():
                weight[label[v]] += w
            # most weight; tie-break by lexicographically smallest label (deterministic)
            best = min(weight.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if label[u] != best:
                label[u] = best
                changed = True
        if not changed:
            break
    groups = defaultdict(list)
    for u, lab in label.items():
        groups[lab].append(u)
    return dict(groups)


def name_group(members, nodes):
    """Readable label: longest common path prefix of members, else biggest member."""
    paths = [nodes[u]["path"].split("/") for u in members]
    common = []
    for segs in zip(*paths):
        if len(set(segs)) == 1:
            common.append(segs[0])
        else:
            break
    if common:
        return "/".join(common)
    biggest = max(members, key=lambda u: nodes[u]["loc"])
    return f"~{nodes[biggest]['path']}"


def relabel(groups, nodes):
    out = {}
    for members in groups.values():
        out[name_group(members, nodes)] = sorted(members)
    return out


# ---------- candidate 2: dependency-graph community ----------

def depgraph_clusters(nodes, hub_pct=0.90):
    """Label propagation on the undirected dependency graph, with HUB-DAMPING:
    edges pointing at the most-depended-on components (>= the `hub_pct` percentile of
    in-degree) are dropped before clustering. Without this, utility hubs that
    everything imports (a `util`/`ui`/`store`) glue the whole graph into one giant
    community — the classic LPA failure mode. Damped hubs fall out as singletons,
    which is honest: a module everyone uses belongs to no single subsystem."""
    indeg = Counter()
    for n in nodes.values():
        for d in n["deps"]:
            indeg[d] += 1
    vals = sorted(indeg.values())
    thr = max(vals[int(hub_pct * (len(vals) - 1))] if vals else 0, 5)
    hubs = {u for u, c in indeg.items() if c >= thr}

    adj = defaultdict(Counter)
    for n in nodes.values():
        for d in n["deps"]:
            if d in hubs:
                continue
            adj[n["uid"]][d] += 1
            adj[d][n["uid"]] += 1
    groups = relabel(label_propagation(list(nodes), adj), nodes)
    return groups, sorted(nodes[h]["path"] for h in hubs)


# ---------- candidate 3: git co-change ----------

def git_cochange_clusters(nodes, repo, window, cochange_min, cochange_frac=0.35):
    # map repo-relative file path -> component uid by longest component-path prefix
    comp_paths = sorted(((n["path"], n["uid"]) for n in nodes.values()),
                        key=lambda kv: -len(kv[0]))

    def file_to_comp(f):
        for path, uid in comp_paths:
            if f == path or f.startswith(path + "/"):
                return uid
        return None

    try:
        out = subprocess.run(
            ["git", "-C", repo, "log", "--no-merges", f"--max-count={window}",
             "--name-only", "--pretty=format:__C__%H"],
            capture_output=True, text=True, check=True).stdout
    except Exception as e:
        print(f"git co-change unavailable: {e}", file=sys.stderr)
        return {}, {"commits": 0, "pairs": 0}

    commits, cur = [], None
    for line in out.splitlines():
        if line.startswith("__C__"):
            cur = set(); commits.append(cur)
        elif line.strip() and cur is not None:
            c = file_to_comp(line.strip())
            if c:
                cur.add(c)

    pair, freq = Counter(), Counter()
    for comps in commits:
        cl = sorted(comps)
        for c in cl:
            freq[c] += 1
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                pair[(cl[i], cl[j])] += 1

    # Normalized association (de-hubs the always-active core): keep an edge only when
    # the two co-change in a meaningful FRACTION of the rarer one's commits — i.e.
    # "when the smaller of the two changes, the other changes with it >= frac of the
    # time" — gated by a raw floor so a 1/1 coincidence can't score 1.0.
    adj = defaultdict(Counter)
    kept = 0
    for (a, b), c in pair.items():
        if c < cochange_min:
            continue
        score = c / max(1, min(freq[a], freq[b]))
        if score >= cochange_frac:
            w = int(round(score * 100))
            adj[a][b] += w; adj[b][a] += w; kept += 1
    groups = relabel(label_propagation(list(nodes), adj), nodes)
    return groups, {"commits": len(commits), "pairs": kept, "window": window,
                    "cochange_min": cochange_min, "cochange_frac": cochange_frac}


# ---------- main ----------

def summarize(name, groups, nodes):
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"  {name:12s} {len(groups):3d} groups ({len(multi)} multi-member, "
          f"{len(groups)-len(multi)} singletons)", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="spine merge output (model.json)")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--git-window", type=int, default=2000)
    ap.add_argument("--cochange-min", type=int, default=4)
    a = ap.parse_args()

    nodes = json.load(open(a.model))["nodes"]
    print(f"loaded {len(nodes)} components from {a.model}", file=sys.stderr)

    folder = folder_clusters(nodes)
    depg, hubs = depgraph_clusters(nodes)
    gitc, gitstats = git_cochange_clusters(nodes, a.repo, a.git_window, a.cochange_min)

    print("candidate clusterings:", file=sys.stderr)
    summarize("folder", folder, nodes)
    summarize("depgraph", depg, nodes)
    summarize("gitcochange", gitc, nodes)
    print(f"  depgraph hubs (damped): {hubs}", file=sys.stderr)
    print(f"  git: {gitstats}", file=sys.stderr)

    # candidates only — the scout reads nodes/signatures from model.json
    json.dump({"candidates": {"folder": folder, "depgraph": depg, "gitcochange": gitc},
               "depgraph_hubs": hubs, "git_stats": gitstats},
              open(a.out, "w"), indent=2)
    print(f"wrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
