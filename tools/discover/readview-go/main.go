// readview — reference implementation of a Go "read-view" extractor: it derives
// a compressed, always-fresh map of a codebase PURELY from source. Nothing here
// is maintained or stored as truth; it is regenerated on demand and thrown away.
// Language structure comes from go/doc; optional routes/migrations come from
// example surface extractors. See wf/docs/read-view-extractor-guide.md.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/doc"
	"go/parser"
	"go/printer"
	"go/token"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

type Pkg struct {
	Rel        string // internal/engine
	Dir        string
	ImportPath string
	Synopsis   string
	LOC        int
	Deps       []string // short names of internal deps
	HasDoc     bool     // package has a doc comment (recorded intent)
	HasTests   bool     // package has _test.go files
	Intent     string   // overlaid from the intent file (authored "why")
	Serves     []string // need IDs this component serves (from intent file)
	doc        *doc.Package
	fset       *token.FileSet
}

// Intent is the only maintained artifact: what code cannot self-report.
type Intent struct {
	Needs     map[string]string // id -> title
	NeedOrder []string
	Comp      map[string]struct {
		Text   string
		Serves []string
	}
	Decisions []struct {
		ID      string
		Affects []string
		Text    string
	}
}

func main() {
	repo := flag.String("repo", ".", "repo root")
	mode := flag.String("mode", "system", "system|component|diagram|html|ir")
	comp := flag.String("component", "", "component (package) name for component mode")
	intentPath := flag.String("intent", "", "optional intent-overlay file")
	rootsArg := flag.String("roots", ".", "comma-separated dirs (under -repo) to scan for packages")
	gomod := flag.String("gomod", "go.mod", "path (under -repo) to go.mod, for module identity")
	routesFile := flag.String("routes", "", "optional file scanned for HTTP routes (example surface extractor)")
	migrationsDir := flag.String("migrations", "", "optional migrations dir (example surface extractor)")
	flag.Parse()

	gomodPath := filepath.Join(*repo, *gomod)
	module := readModule(gomodPath)
	moduleRoot := filepath.Dir(gomodPath)
	var roots []string
	for _, r := range strings.Split(*rootsArg, ",") {
		if r = strings.TrimSpace(r); r != "" {
			roots = append(roots, r)
		}
	}
	pkgs := scanPackages(*repo, moduleRoot, module, roots)

	var intent *Intent
	var stale []string
	if *intentPath != "" {
		if intent = loadIntent(*intentPath); intent != nil {
			stale = attachIntent(pkgs, intent)
		}
	}

	var out bytes.Buffer
	var srcLOC int
	switch *mode {
	case "system":
		srcLOC = renderSystem(&out, *repo, module, pkgs, *routesFile, *migrationsDir)
	case "component":
		srcLOC = renderComponent(&out, pkgs, *comp)
	case "diagram":
		srcLOC = renderDiagram(&out, pkgs)
	case "html":
		srcLOC = renderHTML(&out, *repo, module, pkgs, intent, stale, *routesFile, *migrationsDir)
	case "ir":
		srcLOC = renderIR(&out, *repo, module, pkgs)
	default:
		fmt.Fprintln(os.Stderr, "mode must be system|component|diagram|html|ir")
		os.Exit(2)
	}

	viewLines := bytes.Count(out.Bytes(), []byte{'\n'})
	io.Copy(os.Stdout, &out)
	if *mode != "html" && *mode != "ir" {
		ratio := 0.0
		if viewLines > 0 {
			ratio = float64(srcLOC) / float64(viewLines)
		}
		fmt.Printf("\n· source LOC scanned: %d → view lines: %d → %.1fx compression\n", srcLOC, viewLines, ratio)
	}
}

// ---------- scanning ----------

// scanPackages walks each configured root (relative to repo) and parses every
// Go package it finds. Roots are configuration, never hardcoded layout.
func scanPackages(repo, moduleRoot, module string, roots []string) []*Pkg {
	var out []*Pkg
	for _, root := range roots {
		filepath.WalkDir(filepath.Join(repo, root), func(path string, d os.DirEntry, err error) error {
			if err != nil || !d.IsDir() {
				return nil
			}
			if p := parseDir(moduleRoot, module, path); p != nil {
				out = append(out, p)
			}
			return nil
		})
	}
	return out
}

func parseDir(moduleRoot, module, dir string) *Pkg {
	fset := token.NewFileSet()
	pkgsMap, err := parser.ParseDir(fset, dir, func(fi os.FileInfo) bool {
		return !strings.HasSuffix(fi.Name(), "_test.go")
	}, parser.ParseComments)
	if err != nil || len(pkgsMap) == 0 {
		return nil
	}
	var astPkg *ast.Package
	for n, p := range pkgsMap {
		if strings.HasSuffix(n, "_test") {
			continue
		}
		astPkg = p
		break
	}
	if astPkg == nil {
		return nil
	}
	rel, _ := filepath.Rel(moduleRoot, dir)
	rel = filepath.ToSlash(rel)
	importPath := module
	if rel != "." {
		importPath = module + "/" + rel
	}
	dp := doc.New(astPkg, importPath, doc.AllDecls)

	loc := 0
	deps := map[string]bool{}
	for fname, f := range astPkg.Files {
		loc += countLines(fname)
		for _, imp := range f.Imports {
			ip := strings.Trim(imp.Path.Value, `"`)
			if strings.HasPrefix(ip, module+"/") { // intra-module import = a dependency edge
				deps[lastSeg(ip)] = true
			}
		}
	}
	syn := synopsis(dp.Doc)
	if syn == "" {
		syn = inferSynopsis(dp)
	}
	hasTests := false
	if ents, err := os.ReadDir(dir); err == nil {
		for _, e := range ents {
			if strings.HasSuffix(e.Name(), "_test.go") {
				hasTests = true
				break
			}
		}
	}
	return &Pkg{Rel: rel, Dir: dir, ImportPath: importPath, Synopsis: syn, LOC: loc,
		Deps: keys(deps), HasDoc: strings.TrimSpace(dp.Doc) != "", HasTests: hasTests, doc: dp, fset: fset}
}

// ---------- system view ----------

func renderSystem(w io.Writer, repo, module string, pkgs []*Pkg, routesFile, migrationsDir string) int {
	total := 0
	for _, p := range pkgs {
		total += p.LOC
	}
	var migs []string
	if migrationsDir != "" {
		migs = migrations(filepath.Join(repo, migrationsDir))
	}
	var routes [][3]string
	if routesFile != "" {
		routes = scanRoutes(filepath.Join(repo, routesFile))
	}

	fmt.Fprintf(w, "SYSTEM READ-VIEW · %s\n", module)
	fmt.Fprintf(w, "generated from code — disposable, regenerate on demand · %d packages · %d LOC\n\n", len(pkgs), total)

	fmt.Fprintln(w, "PACKAGE MAP  (by mass; → = intra-repo deps)")
	byLOC := append([]*Pkg(nil), pkgs...)
	sort.Slice(byLOC, func(i, j int) bool { return byLOC[i].LOC > byLOC[j].LOC })
	for _, p := range byLOC {
		fmt.Fprintf(w, "  %-22s %5d  %s\n", p.Rel, p.LOC, p.Synopsis)
		if len(p.Deps) > 0 {
			fmt.Fprintf(w, "  %-22s        → %s\n", "", strings.Join(p.Deps, ", "))
		}
	}
	fmt.Fprintln(w)

	if len(routes) > 0 {
		fmt.Fprintf(w, "HTTP API SURFACE  (%d routes)\n", len(routes))
		for _, r := range routes {
			fmt.Fprintf(w, "  %-7s %-40s %s\n", r[0], r[1], r[2])
		}
		fmt.Fprintln(w)
	}

	if len(migs) > 0 {
		fmt.Fprintf(w, "DATA MODEL EVOLUTION  (%d migrations — newest %d)\n", len(migs), min(14, len(migs)))
		for _, m := range migs[:min(14, len(migs))] {
			fmt.Fprintf(w, "  %s\n", m)
		}
		fmt.Fprintln(w)
	}

	return total
}

// ---------- component view ----------

func renderComponent(w io.Writer, pkgs []*Pkg, comp string) int {
	var p *Pkg
	for _, c := range pkgs {
		if lastSeg(c.Rel) == comp || c.Rel == comp {
			p = c
			break
		}
	}
	if p == nil {
		fmt.Fprintf(w, "no component matching %q\n", comp)
		return 0
	}
	fmt.Fprintf(w, "COMPONENT READ-VIEW · %s   (generated from code — disposable)\n", comp)
	fmt.Fprintf(w, "package %s · %d LOC source\n", p.Rel, p.LOC)
	if p.Synopsis != "" {
		fmt.Fprintf(w, "synopsis: %s\n", p.Synopsis)
	}
	if len(p.Deps) > 0 {
		fmt.Fprintf(w, "depends on (internal): %s\n", strings.Join(p.Deps, ", "))
	}
	fmt.Fprintln(w)

	if len(p.doc.Types) > 0 {
		fmt.Fprintln(w, "TYPES")
		for _, t := range p.doc.Types {
			kind, members := typeShape(p.fset, t)
			fmt.Fprintf(w, "  %s %s", t.Name, kind)
			if s := synopsis(t.Doc); s != "" {
				fmt.Fprintf(w, "  — %s", s)
			}
			fmt.Fprintln(w)
			for _, m := range members {
				fmt.Fprintf(w, "      %s\n", m)
			}
			for _, fn := range t.Funcs { // constructors
				fmt.Fprintf(w, "      %s\n", funcSig(p.fset, fn))
			}
			for _, m := range t.Methods {
				line := funcSig(p.fset, m)
				if s := synopsis(m.Doc); s != "" {
					line += "  — " + s
				}
				fmt.Fprintf(w, "      %s\n", line)
			}
		}
		fmt.Fprintln(w)
	}

	if len(p.doc.Funcs) > 0 {
		fmt.Fprintln(w, "FUNCTIONS")
		for _, fn := range p.doc.Funcs {
			line := funcSig(p.fset, fn)
			if s := synopsis(fn.Doc); s != "" {
				line += "  — " + s
			}
			fmt.Fprintf(w, "  %s\n", line)
		}
		fmt.Fprintln(w)
	}

	fmt.Fprintln(w, "(unexported symbols and bodies omitted — read source for implementation)")
	return p.LOC
}

// ---------- diagram view (human conceptualization) ----------

func renderDiagram(w io.Writer, pkgs []*Pkg) int {
	total := 0
	byName := map[string]*Pkg{}
	for _, p := range pkgs {
		total += p.LOC
		byName[lastSeg(p.Rel)] = p
	}
	// layer = 0 for leaves, else 1 + max(layer of internal deps)
	layer := map[string]int{}
	var depth func(name string, seen map[string]bool) int
	depth = func(name string, seen map[string]bool) int {
		if v, ok := layer[name]; ok {
			return v
		}
		p := byName[name]
		if p == nil || seen[name] {
			return 0
		}
		seen[name] = true
		max := -1
		for _, d := range p.Deps {
			if l := depth(d, seen); l > max {
				max = l
			}
		}
		layer[name] = max + 1
		return layer[name]
	}
	maxLayer := 0
	for n := range byName {
		if l := depth(n, map[string]bool{}); l > maxLayer {
			maxLayer = l
		}
	}

	marks := func(p *Pkg) string {
		var m []string
		if strings.HasPrefix(p.Rel, "cmd/") {
			m = append(m, "entry")
		}
		if len(p.Deps) == 0 {
			m = append(m, "leaf")
		}
		if !p.HasDoc {
			m = append(m, "[!]no-intent")
		}
		if !p.HasTests {
			m = append(m, "[t!]no-tests")
		}
		if p.LOC > 1500 {
			m = append(m, "[*]large")
		}
		if len(m) == 0 {
			return ""
		}
		return "  " + strings.Join(m, " ")
	}

	fmt.Fprintln(w, "COMPONENT DEPENDENCY GRAPH   (derived from import edges · top = entry, bottom = foundation)")
	fmt.Fprintln(w, "legend:  [!]no-intent = no package doc (no recorded \"why\")   [t!]no-tests   [*]large   → depends on")
	fmt.Fprintln(w)
	for l := maxLayer; l >= 0; l-- {
		var names []string
		for n, lv := range layer {
			if lv == l {
				names = append(names, n)
			}
		}
		sort.Strings(names)
		if len(names) == 0 {
			continue
		}
		fmt.Fprintf(w, "L%d\n", l)
		for _, n := range names {
			p := byName[n]
			fmt.Fprintf(w, "   %-14s%s\n", p.Rel, marks(p))
			if len(p.Deps) > 0 {
				fmt.Fprintf(w, "   %-14s   → %s\n", "", strings.Join(p.Deps, " "))
			}
		}
		fmt.Fprintln(w)
	}

	// Mermaid rendering — the richer human view (renders in the HTML viewer / claude.ai / GitHub)
	fmt.Fprintln(w, "--- same graph as Mermaid (paste into the spec HTML viewer / GitHub / claude.ai) ---")
	fmt.Fprintln(w, "```mermaid")
	fmt.Fprint(w, mermaidOf(pkgs))
	fmt.Fprintln(w, "```")
	return total
}

// ---------- common IR (the contract the language-agnostic spine consumes) ----------

type IR struct {
	Module     string        `json:"module"`
	Language   string        `json:"language"`
	Components []IRComponent `json:"components"`
}

type IRComponent struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	Path      string   `json:"path"`
	LOC       int      `json:"loc"`
	Kind      string   `json:"kind"`
	Synopsis  string   `json:"synopsis"`
	DependsOn []string `json:"depends_on"`
	HasDoc    bool     `json:"has_doc"`
	HasTests  bool     `json:"has_tests"`
	Types     []IRType `json:"types,omitempty"`
	Functions []IRFunc `json:"functions,omitempty"`
}

type IRType struct {
	Name    string   `json:"name"`
	Kind    string   `json:"kind"`
	Doc     string   `json:"doc,omitempty"`
	Members []string `json:"members,omitempty"`
	Methods []string `json:"methods,omitempty"`
}

type IRFunc struct {
	Name      string `json:"name"`
	Signature string `json:"signature"`
	Doc       string `json:"doc,omitempty"`
}

// buildIR is the whole "code → IR" path: it reshapes the go/doc model the
// toolchain already produced into the language-neutral IR. No parsing here —
// parsing happened in parseDir via go/parser; this only reshapes.
func buildIR(repo, module string, pkgs []*Pkg) IR {
	ir := IR{Module: module, Language: "go"}
	for _, p := range pkgs {
		deps := p.Deps
		if deps == nil {
			deps = []string{}
		}
		c := IRComponent{
			ID:        p.Rel,
			Name:      lastSeg(p.Rel),
			Path:      strings.TrimPrefix(p.Dir, repo+"/"),
			LOC:       p.LOC,
			Kind:      "package",
			Synopsis:  p.Synopsis,
			DependsOn: deps,
			HasDoc:    p.HasDoc,
			HasTests:  p.HasTests,
		}
		for _, t := range p.doc.Types {
			kind, members := typeShape(p.fset, t)
			if strings.HasPrefix(kind, "= ") {
				kind = "alias"
			}
			it := IRType{Name: t.Name, Kind: kind, Doc: synopsis(t.Doc), Members: members}
			for _, fn := range t.Funcs { // constructors count as methods of the type
				it.Methods = append(it.Methods, funcSig(p.fset, fn))
			}
			for _, m := range t.Methods {
				it.Methods = append(it.Methods, funcSig(p.fset, m))
			}
			c.Types = append(c.Types, it)
		}
		for _, fn := range p.doc.Funcs {
			c.Functions = append(c.Functions, IRFunc{
				Name: fn.Name, Signature: funcSig(p.fset, fn), Doc: synopsis(fn.Doc),
			})
		}
		ir.Components = append(ir.Components, c)
	}
	return ir
}

func renderIR(w io.Writer, repo, module string, pkgs []*Pkg) int {
	b, _ := json.MarshalIndent(buildIR(repo, module, pkgs), "", "  ")
	w.Write(b)
	fmt.Fprintln(w)
	total := 0
	for _, p := range pkgs {
		total += p.LOC
	}
	return total
}

// ---------- html view (one self-contained human page) ----------

func renderHTML(w io.Writer, repo, module string, pkgs []*Pkg, intent *Intent, stale []string, routesFile, migrationsDir string) int {
	total := 0
	for _, p := range pkgs {
		total += p.LOC
	}
	var migs []string
	if migrationsDir != "" {
		migs = migrations(filepath.Join(repo, migrationsDir))
	}
	var routes [][3]string
	if routesFile != "" {
		routes = scanRoutes(filepath.Join(repo, routesFile))
	}
	byLOC := append([]*Pkg(nil), pkgs...)
	sort.Slice(byLOC, func(i, j int) bool { return byLOC[i].LOC > byLOC[j].LOC })

	fmt.Fprintf(w, `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Read-view · %s</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>document.addEventListener('DOMContentLoaded',()=>mermaid.initialize({startOnLoad:true,securityLevel:'loose'}));</script>
<style>
:root{--bg:#fbfbfd;--card:#fff;--ink:#1c2330;--muted:#6b7384;--line:#e4e7ee;--warn:#c0263b;--warnbg:#fdeef0;--info:#0a66c2;--infobg:#e8f1fb}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:24px;margin:0 0 2px}h2{font-size:17px;margin:38px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px;margin-bottom:8px}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px}
.stack{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}
.stack div{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;font-size:13px}
.stack b{display:block;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
table{width:100%%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f4f6fa;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
tr:last-child td{border-bottom:none}
.num{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:1px 7px;border-radius:20px;margin-right:4px;white-space:nowrap}
.b-warn{background:var(--warnbg);color:var(--warn)}.b-info{background:var(--infobg);color:var(--info)}.b-mut{background:#eef0f4;color:var(--muted)}
.b-ok{background:#e9f7ef;color:#1e8e54}.b-need{background:#eef3fb;color:#0a66c2;font-weight:600}
ul.recon{margin:2px 0 6px;padding-left:22px}ul.recon li{margin:2px 0}
.mermaid{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:0;text-align:center}
.method{font-weight:700;font-size:11px;padding:1px 6px;border-radius:5px;background:#eef0f4;color:#3a4150}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:4px 14px}
summary{cursor:pointer;font-weight:600;padding:8px 0;font-size:14px}summary code{color:var(--muted);font-weight:400}
.tname{font-weight:700}.member{color:#384;display:block;margin-left:18px}.syn{color:var(--muted)}
.legend{font-size:12px;color:var(--muted);margin:8px 0 0}
</style></head><body><div class="wrap">`, html(module))

	// header + stack
	fmt.Fprintf(w, `<h1>Read-view · %s</h1>
<div class="sub">Generated from source — disposable, regenerate on demand. %d LOC → this page.</div>
<div class="stack">
<div><b>Module</b>Go · %d packages · %d LOC</div>
<div><b>Migrations</b>%d</div>
<div><b>HTTP routes</b>%d</div>
</div>`, html(module), total, len(pkgs), total, len(migs), len(routes))

	// dependency graph
	fmt.Fprintln(w, `<h2>Architecture — component dependency graph</h2>`)
	if intent != nil {
		fmt.Fprintln(w, `<p class="legend">Edges derived from code; colour from the intent overlay. <span class="badge b-ok">green</span> = intent recorded (need shown) · <span class="badge b-warn">red</span> = uncovered · <span class="badge b-info">blue</span> = entry point.</p>`)
	} else {
		fmt.Fprintln(w, `<p class="legend">Derived from import edges. <span class="badge b-warn">red</span> = no recorded intent (no package doc) · <span class="badge b-info">blue</span> = entry point.</p>`)
	}
	fmt.Fprint(w, `<pre class="mermaid">`)
	fmt.Fprint(w, mermaidOf(pkgs))
	fmt.Fprintln(w, `</pre>`)

	// package map — overlaid intent wins over derived synopsis when present
	whoCol := "Synopsis (derived)"
	if intent != nil {
		whoCol = "Intent / serves"
	}
	fmt.Fprintf(w, `<h2>Package map</h2><table><tr><th>Package</th><th class="num">LOC</th><th>%s</th><th>Depends on</th><th>Health</th></tr>`, whoCol)
	for _, p := range byLOC {
		who := html(p.Synopsis)
		if p.Intent != "" {
			who = html(p.Intent)
			if len(p.Serves) > 0 {
				who += ` ` + serveBadges(p, intent)
			}
		}
		fmt.Fprintf(w, `<tr><td><code>%s</code></td><td class="num">%d</td><td>%s</td><td><code>%s</code></td><td>%s</td></tr>`,
			html(p.Rel), p.LOC, who, html(strings.Join(p.Deps, ", ")), badges(p))
	}
	fmt.Fprintln(w, `</table>`)

	// intent reconciliation — the anti-rot dashboard (only with an overlay)
	if intent != nil {
		renderReconcile(w, pkgs, intent, stale)
	}

	// api surface (example surface extractor — only when -routes is given)
	if len(routes) > 0 {
		fmt.Fprintln(w, `<h2>HTTP API surface</h2><table><tr><th>Method</th><th>Path</th><th>Handler</th></tr>`)
		for _, r := range routes {
			fmt.Fprintf(w, `<tr><td><span class="method">%s</span></td><td><code>%s</code></td><td><code>%s</code></td></tr>`,
				html(r[0]), html(r[1]), html(r[2]))
		}
		fmt.Fprintln(w, `</table>`)
	}

	// components detail
	fmt.Fprintln(w, `<h2>Components</h2>`)
	for _, p := range byLOC {
		fmt.Fprintf(w, `<details><summary>%s <code>· %d LOC</code> %s</summary>`, html(lastSeg(p.Rel)), p.LOC, badges(p))
		if p.Intent != "" {
			fmt.Fprintf(w, `<p class="syn" style="margin:6px 0 10px"><b>Intent:</b> %s %s</p>`, html(p.Intent), serveBadges(p, intent))
		}
		if len(p.doc.Types) > 0 {
			for _, t := range p.doc.Types {
				kind, members := typeShape(p.fset, t)
				fmt.Fprintf(w, `<div><span class="tname">%s</span> <span class="syn">%s</span>`, html(t.Name), html(kind))
				if s := synopsis(t.Doc); s != "" {
					fmt.Fprintf(w, ` <span class="syn">— %s</span>`, html(s))
				}
				for _, m := range members {
					fmt.Fprintf(w, `<code class="member">%s</code>`, html(m))
				}
				for _, fn := range t.Funcs {
					fmt.Fprintf(w, `<code class="member">%s</code>`, html(funcSig(p.fset, fn)))
				}
				for _, m := range t.Methods {
					fmt.Fprintf(w, `<code class="member">%s</code>`, html(funcSig(p.fset, m)))
				}
				fmt.Fprintln(w, `</div>`)
			}
		}
		if len(p.doc.Funcs) > 0 {
			fmt.Fprintln(w, `<div style="margin-top:8px"><span class="syn">functions</span>`)
			for _, fn := range p.doc.Funcs {
				fmt.Fprintf(w, `<code class="member">%s</code>`, html(funcSig(p.fset, fn)))
			}
			fmt.Fprintln(w, `</div>`)
		}
		fmt.Fprintln(w, `</details>`)
	}

	// migrations (example surface extractor — only when -migrations is given)
	if len(migs) > 0 {
		fmt.Fprintf(w, `<h2>Data model evolution <code style="font-weight:400;color:var(--muted)">(%d migrations, newest first)</code></h2><table><tr><th>#</th><th>Migration</th></tr>`, len(migs))
		for _, m := range migs {
			parts := strings.SplitN(m, "  ", 2)
			if len(parts) == 2 {
				fmt.Fprintf(w, `<tr><td class="mono">%s</td><td>%s</td></tr>`, html(parts[0]), html(parts[1]))
			}
		}
		fmt.Fprintln(w, `</table>`)
	}

	fmt.Fprintln(w, `</div></body></html>`)
	return total
}

func mermaidOf(pkgs []*Pkg) string {
	byName := map[string]*Pkg{}
	var names []string
	for _, p := range pkgs {
		byName[lastSeg(p.Rel)] = p
	}
	for n := range byName {
		names = append(names, n)
	}
	sort.Strings(names)
	var b strings.Builder
	b.WriteString("graph TD\n")
	for _, n := range names {
		p := byName[n]
		cls := ""
		switch {
		case strings.HasPrefix(p.Rel, "cmd/"):
			cls = ":::entry"
		case p.Intent != "":
			cls = ":::hasintent"
		case !p.HasDoc:
			cls = ":::nointent"
		}
		label := p.Rel
		if len(p.Serves) > 0 {
			label += "<br/><small>" + strings.Join(p.Serves, ", ") + "</small>"
		}
		fmt.Fprintf(&b, "  %s[\"%s<br/>%d LOC\"]%s\n", nodeID(n), label, p.LOC, cls)
	}
	for _, n := range names {
		for _, d := range byName[n].Deps {
			if _, ok := byName[d]; ok {
				fmt.Fprintf(&b, "  %s --> %s\n", nodeID(n), nodeID(d))
			}
		}
	}
	b.WriteString("  classDef hasintent fill:#e9f7ef,stroke:#1e8e54,color:#0c5a33\n")
	b.WriteString("  classDef nointent fill:#fdeef0,stroke:#c0263b,color:#7a0a1c\n")
	b.WriteString("  classDef entry fill:#e8f1fb,stroke:#0a66c2,color:#063a73\n")
	return b.String()
}

func badges(p *Pkg) string {
	var b strings.Builder
	if strings.HasPrefix(p.Rel, "cmd/") {
		b.WriteString(`<span class="badge b-info">entry</span>`)
	}
	if len(p.Deps) == 0 {
		b.WriteString(`<span class="badge b-mut">leaf</span>`)
	}
	// intent overlay wins: covered → green, genuinely uncovered → red.
	switch {
	case p.Intent != "":
		b.WriteString(`<span class="badge b-ok">intent</span>`)
	case !p.HasDoc:
		b.WriteString(`<span class="badge b-warn">no-intent</span>`)
	}
	if !p.HasTests {
		b.WriteString(`<span class="badge b-mut">no-tests</span>`)
	}
	if p.LOC > 1500 {
		b.WriteString(`<span class="badge b-info">large</span>`)
	}
	return b.String()
}

// serveBadges renders a component's served-need IDs, each titled from the intent.
func serveBadges(p *Pkg, in *Intent) string {
	var b strings.Builder
	for _, id := range p.Serves {
		title := ""
		if in != nil {
			title = in.Needs[id]
		}
		fmt.Fprintf(&b, `<span class="badge b-need" title="%s">%s</span>`, html(title), html(id))
	}
	return b.String()
}

// renderReconcile is the anti-rot dashboard: it diffs the maintained intent
// against the derived component set. All findings are recomputed from current
// code + the tiny intent file — no stored baseline, no drift ledger.
func renderReconcile(w io.Writer, pkgs []*Pkg, in *Intent, stale []string) {
	var uncovered []string
	covered := 0
	served := map[string]bool{}
	needComps := map[string][]string{}
	for _, p := range pkgs {
		if strings.HasPrefix(p.Rel, "cmd/") {
			continue
		}
		if p.Intent == "" {
			uncovered = append(uncovered, p.Rel)
			continue
		}
		covered++
		for _, id := range p.Serves {
			served[id] = true
			needComps[id] = append(needComps[id], lastSeg(p.Rel))
		}
	}
	var unbuilt []string
	for _, id := range in.NeedOrder {
		if !served[id] {
			unbuilt = append(unbuilt, id)
		}
	}
	total := covered + len(uncovered)

	fmt.Fprintln(w, `<h2>Intent reconciliation <code style="font-weight:400;color:var(--muted)">(maintained intent ⟷ derived code)</code></h2>`)
	fmt.Fprintf(w, `<div class="stack">
<div><b>Intent coverage</b>%d / %d components</div>
<div><b>Uncovered</b>%d (code, no intent)</div>
<div><b>Stale intent</b>%d (intent, no code)</div>
<div><b>Unbuilt needs</b>%d (need, no component)</div>
</div>`, covered, total, len(uncovered), len(stale), len(unbuilt))

	findings := func(title, cls string, items []string, note string) {
		if len(items) == 0 {
			return
		}
		fmt.Fprintf(w, `<p style="margin:10px 0 4px"><span class="badge %s">%s</span> <span class="syn">%s</span></p><ul class="recon">`, cls, html(title), html(note))
		for _, it := range items {
			fmt.Fprintf(w, `<li><code>%s</code></li>`, html(it))
		}
		fmt.Fprintln(w, `</ul>`)
	}
	findings("uncovered component", "b-warn", uncovered, "exists in code, no recorded why — fill in the intent file or delete the code")
	findings("stale intent", "b-warn", stale, "named in intent, no such component in code — rename or remove the intent entry")
	var unbuiltLbl []string
	for _, id := range unbuilt {
		unbuiltLbl = append(unbuiltLbl, id+" — "+in.Needs[id])
	}
	findings("unbuilt need", "b-info", unbuiltLbl, "declared need, nothing serves it — backlog or descope")

	// need → components traceability
	fmt.Fprintln(w, `<h2>Need → components</h2><table><tr><th>Need</th><th>Title</th><th>Served by</th></tr>`)
	for _, id := range in.NeedOrder {
		comps := needComps[id]
		served := html(strings.Join(comps, ", "))
		if len(comps) == 0 {
			served = `<span class="badge b-info">unbuilt</span>`
		}
		fmt.Fprintf(w, `<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>`, html(id), html(in.Needs[id]), served)
	}
	fmt.Fprintln(w, `</table>`)

	// deliberate decisions
	if len(in.Decisions) > 0 {
		fmt.Fprintln(w, `<h2>Deliberate decisions <code style="font-weight:400;color:var(--muted)">(ADR-lite)</code></h2><table><tr><th>ID</th><th>Decision</th><th>Affects</th></tr>`)
		for _, d := range in.Decisions {
			fmt.Fprintf(w, `<tr><td><code>%s</code></td><td>%s</td><td><code>%s</code></td></tr>`, html(d.ID), html(d.Text), html(strings.Join(d.Affects, ", ")))
		}
		fmt.Fprintln(w, `</table>`)
	}
}

// nodeID namespaces a package short-name into a Mermaid-safe identifier,
// so reserved keywords (graph, end, class, …) and path chars can't break the parser.
func nodeID(name string) string {
	var b strings.Builder
	b.WriteString("c_")
	for _, r := range name {
		if r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' {
			b.WriteRune(r)
		} else {
			b.WriteByte('_')
		}
	}
	return b.String()
}

func html(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	return s
}

// typeShape returns the kind ("struct"/"interface"/"") and member lines.
func typeShape(fset *token.FileSet, t *doc.Type) (string, []string) {
	if t.Decl == nil || len(t.Decl.Specs) == 0 {
		return "", nil
	}
	ts, ok := t.Decl.Specs[0].(*ast.TypeSpec)
	if !ok {
		return "", nil
	}
	switch u := ts.Type.(type) {
	case *ast.StructType:
		var out []string
		for _, f := range u.Fields.List {
			typ := exprStr(fset, f.Type)
			if len(f.Names) == 0 { // embedded
				out = append(out, typ+"  (embedded)")
				continue
			}
			for _, n := range f.Names {
				if n.IsExported() {
					out = append(out, n.Name+" "+typ)
				}
			}
		}
		return "struct", out
	case *ast.InterfaceType:
		var out []string
		for _, m := range u.Methods.List {
			if ft, ok := m.Type.(*ast.FuncType); ok && len(m.Names) > 0 {
				out = append(out, m.Names[0].Name+exprStr(fset, ft)[len("func"):])
			}
		}
		return "interface", out
	default:
		return "= " + exprStr(fset, ts.Type), nil
	}
}

func funcSig(fset *token.FileSet, fn *doc.Func) string {
	if fn.Decl == nil {
		return fn.Name + "(…)"
	}
	cp := *fn.Decl
	cp.Body = nil
	cp.Doc = nil
	var b bytes.Buffer
	printer.Fprint(&b, fset, &cp)
	return collapse(b.String())
}

func exprStr(fset *token.FileSet, e ast.Expr) string {
	var b bytes.Buffer
	printer.Fprint(&b, fset, e)
	return collapse(b.String())
}

// ---------- example surface extractors ----------
//
// These encode framework/tool CONVENTIONS, not language semantics — included
// only to demonstrate the "surface extractor" pattern from the guide (§8).
// scanRoutes assumes a chi-style router; migrations assumes sequentially
// numbered "NNN_name.up.sql" files. Both are opt-in (-routes / -migrations);
// swap the patterns for your own framework/tool.

var routeRe = regexp.MustCompile(`r\.(Get|Post|Put|Delete|Patch)\("([^"]+)",\s*([A-Za-z0-9_.]+)`)

func scanRoutes(mainGo string) [][3]string {
	data, err := os.ReadFile(mainGo)
	if err != nil {
		return nil
	}
	var out [][3]string
	for _, m := range routeRe.FindAllStringSubmatch(string(data), -1) {
		out = append(out, [3]string{strings.ToUpper(m[1]), m[2], m[3]})
	}
	return out
}

var migRe = regexp.MustCompile(`^(\d+)_(.+)\.up\.sql$`)

func migrations(dir string) []string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	type mig struct {
		num  string
		name string
	}
	var ms []mig
	for _, e := range entries {
		if m := migRe.FindStringSubmatch(e.Name()); m != nil {
			ms = append(ms, mig{m[1], strings.ReplaceAll(m[2], "_", " ")})
		}
	}
	sort.Slice(ms, func(i, j int) bool { return ms[i].num > ms[j].num })
	var out []string
	for _, m := range ms {
		out = append(out, m.num+"  "+m.name)
	}
	return out
}

// ---------- helpers ----------

// ---------- intent overlay ----------

// loadIntent parses the line-based intent file. Grammar (one record per line):
//
//	need <ID> <title...>
//	component <name> [serves=ID,ID] <intent text...>
//	decision <ID> [affects=name,name] <text...>
//
// Blank lines and lines starting with # are ignored.
func loadIntent(path string) *Intent {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	in := &Intent{Needs: map[string]string{}, Comp: map[string]struct {
		Text   string
		Serves []string
	}{}}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		verb, rest, _ := strings.Cut(line, " ")
		rest = strings.TrimSpace(rest)
		switch verb {
		case "need":
			id, title, _ := strings.Cut(rest, " ")
			in.Needs[id] = strings.TrimSpace(title)
			in.NeedOrder = append(in.NeedOrder, id)
		case "component":
			name, tail, _ := strings.Cut(rest, " ")
			serves, text := splitAttr(strings.TrimSpace(tail), "serves=")
			in.Comp[name] = struct {
				Text   string
				Serves []string
			}{text, serves}
		case "decision":
			id, tail, _ := strings.Cut(rest, " ")
			affects, text := splitAttr(strings.TrimSpace(tail), "affects=")
			in.Decisions = append(in.Decisions, struct {
				ID      string
				Affects []string
				Text    string
			}{id, affects, text})
		}
	}
	return in
}

// splitAttr peels an optional leading "key=a,b,c" token off the front of s,
// returning its comma-split values and the remaining free text.
func splitAttr(s, key string) ([]string, string) {
	if !strings.HasPrefix(s, key) {
		return nil, s
	}
	tok, text, _ := strings.Cut(strings.TrimPrefix(s, key), " ")
	var vals []string
	for _, v := range strings.Split(tok, ",") {
		if v = strings.TrimSpace(v); v != "" {
			vals = append(vals, v)
		}
	}
	return vals, strings.TrimSpace(text)
}

// attachIntent maps intent component entries onto the derived packages and
// returns the intent entries that matched no real component (stale intent).
func attachIntent(pkgs []*Pkg, in *Intent) (stale []string) {
	matched := map[string]bool{}
	for name, ci := range in.Comp {
		for _, p := range pkgs {
			ck := compKey(p.Rel)
			// candidates cover leaf name, path key, full rel, and the
			// hyphenated convention (importer-csv ⟷ importer/csv).
			if lastSeg(p.Rel) == name || ck == name || p.Rel == name ||
				strings.ReplaceAll(ck, "/", "-") == name {
				p.Intent = ci.Text
				p.Serves = ci.Serves
				matched[name] = true
				break
			}
		}
	}
	for name := range in.Comp {
		if !matched[name] {
			stale = append(stale, name)
		}
	}
	sort.Strings(stale)
	return
}

func compKey(rel string) string { return strings.TrimPrefix(rel, "internal/") }

func readModule(gomod string) string {
	data, err := os.ReadFile(gomod)
	if err != nil {
		return "unknown"
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "module ") {
			return strings.TrimSpace(strings.TrimPrefix(line, "module "))
		}
	}
	return "unknown"
}

func countLines(fname string) int {
	data, err := os.ReadFile(fname)
	if err != nil {
		return 0
	}
	return bytes.Count(data, []byte{'\n'}) + 1
}

func synopsis(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	// strip leading "Package <name> "
	if strings.HasPrefix(s, "Package ") {
		if i := strings.IndexByte(s[8:], ' '); i >= 0 {
			s = s[8+i+1:]
		}
	}
	// cut at first sentence end
	if i := strings.Index(s, ". "); i >= 0 {
		s = s[:i+1]
	}
	return strings.TrimSpace(s)
}

func inferSynopsis(dp *doc.Package) string {
	var names []string
	for _, t := range dp.Types {
		names = append(names, t.Name)
		if len(names) == 4 {
			break
		}
	}
	if len(names) == 0 {
		for _, f := range dp.Funcs {
			names = append(names, f.Name+"()")
			if len(names) == 4 {
				break
			}
		}
	}
	if len(names) == 0 {
		return "(no exported surface)"
	}
	return "exposes: " + strings.Join(names, ", ")
}

func collapse(s string) string { return strings.Join(strings.Fields(s), " ") }
func lastSeg(p string) string  { return p[strings.LastIndexByte(p, '/')+1:] }

func keys(m map[string]bool) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
