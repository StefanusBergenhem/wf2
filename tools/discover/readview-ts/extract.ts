// readview-ts — reference implementation of a TypeScript/JavaScript "read-view"
// extractor: it derives a compressed, always-fresh map of a codebase PURELY from
// source, using the official TypeScript Compiler API (ts.createProgram + the type
// checker). Nothing here is maintained or stored as truth; it is regenerated on
// demand and thrown away. It carries NO project or domain knowledge: layout is
// configuration (flags), never constants.
//
// It emits the common IR (read-view-extractor-guide.md §4) verbatim — the same
// JSON field names/shape the Go reference extractor's `-mode ir` produces — so a
// single language-agnostic spine can union Go + TS IRs into one system view.
//
// See wf/docs/read-view-extractor-guide.md (esp. §4, §7 TS row, Appendix A).

import * as ts from "typescript";
import * as fs from "fs";
import * as path from "path";

// ---------- the common IR (matches read-view-extractor-guide.md §4 verbatim) ----------

interface IR {
  module: string;
  language: string;
  components: IRComponent[];
}

interface IRComponent {
  id: string;
  name: string;
  path: string;
  loc: number;
  kind: string;
  synopsis: string;
  depends_on: string[];
  has_doc: boolean;
  has_tests: boolean;
  types?: IRType[];
  functions?: IRFunc[];
}

interface IRType {
  name: string;
  kind: string; // interface | class | enum | alias
  doc?: string;
  members?: string[];
  methods?: string[];
}

interface IRFunc {
  name: string;
  signature: string;
  doc?: string;
}

// ---------- flags (all layout is configuration, no hardcoded paths) ----------

interface Flags {
  repo: string;
  roots: string[];
  tsconfig: string;
  mode: string;
  exclude: string[]; // glob patterns (repo-relative) whose components are dropped
}

function parseFlags(argv: string[]): Flags {
  const f: Flags = { repo: ".", roots: ["src"], tsconfig: "", mode: "ir", exclude: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i] ?? "";
    switch (a) {
      case "-repo":
      case "--repo":
        f.repo = next();
        break;
      case "-roots":
      case "--roots":
        f.roots = next()
          .split(",")
          .map((s) => s.trim())
          .filter((s) => s !== "");
        break;
      case "-tsconfig":
      case "--tsconfig":
        f.tsconfig = next();
        break;
      case "-mode":
      case "--mode":
        f.mode = next();
        break;
      case "-exclude":
      case "--exclude":
        f.exclude = next()
          .split(",")
          .map((s) => s.trim())
          .filter((s) => s !== "");
        break;
    }
  }
  return f;
}

// ---------- exclude globs (drop generated/vendored dirs; layout config, not code) ----------
//
// `-exclude` takes comma-separated globs matched against a component's repo-
// relative id (the dir path). It is PROJECT-AGNOSTIC: excluding generated/
// vendored trees (protobuf codegen, ORM type dumps, vendored deps) is a need in
// ANY repo, so the mechanism is generic and the patterns are supplied per-run.
// Supported glob tokens: `**` (any path segments incl. none), `*` (any chars
// within a segment), `?` (one char); everything else is literal. A pattern
// matches if it equals the id, the id is under it (prefix on a path boundary),
// or the glob matches the full id.
function compileGlob(pattern: string): RegExp {
  let re = "";
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === "*") {
      if (pattern[i + 1] === "*") {
        re += ".*"; // ** — any path segments, including none
        i++;
        if (pattern[i + 1] === "/") i++; // swallow the trailing slash of `**/`
      } else {
        re += "[^/]*"; // * — within a single segment
      }
    } else if (ch === "?") {
      re += "[^/]";
    } else {
      re += ch.replace(/[.+^${}()|[\]\\]/g, "\\$&");
    }
  }
  return new RegExp("^" + re + "$");
}

function isExcluded(id: string, globs: RegExp[], rawPatterns: string[]): boolean {
  // a bare dir pattern excludes that dir AND everything under it (a path prefix).
  for (const p of rawPatterns) {
    const base = p.replace(/\/\*\*$/, "").replace(/\/$/, "");
    if (id === base || id.startsWith(base + "/")) return true;
  }
  return globs.some((g) => g.test(id));
}

// ---------- component model (internal, richer than the IR) ----------
//
// Unit of "component" = a DIRECTORY containing >=1 non-test, non-declaration
// .ts/.tsx source file. This mirrors the Go reference's "one component per
// package/dir". `id` = repo-relative dir path (stable — the intent overlay
// matches on it). Subdirectories are their OWN components (a dir owns its own
// files only; it does not absorb nested dirs), so a parent dir and its child
// dir are two separate components.

interface Comp {
  id: string; // repo-relative dir, e.g. "src/api"
  dir: string; // absolute dir
  files: string[]; // absolute non-test source files in THIS dir
  loc: number;
  hasTests: boolean;
  hasDoc: boolean;
  synopsis: string;
  deps: Set<string>; // resolved component ids (intra-repo)
  types: IRType[];
  functions: IRFunc[];
}

const isTestFile = (f: string) =>
  /\.(test|spec)\.[cm]?[jt]sx?$/.test(f) || /(^|[\\/])__tests__[\\/]/.test(f);
const isDecl = (f: string) => f.endsWith(".d.ts");
const isSource = (f: string) =>
  /\.[cm]?[jt]sx?$/.test(f) && !isTestFile(f) && !isDecl(f);

// ---------- main ----------

function main() {
  const flags = parseFlags(process.argv.slice(2));
  const repo = path.resolve(flags.repo);

  // Module identity: derive from package.json near the tsconfig / repo (don't
  // hardcode). Falls back to the repo dir name.
  const module = deriveModuleName(repo, flags.tsconfig);

  const program = createProgram(repo, flags);
  const checker = program.getTypeChecker();

  // Compile the exclude globs once (matched against repo-relative component ids).
  const excludeGlobs = flags.exclude.map(compileGlob);
  const excluded = (id: string) => isExcluded(id, excludeGlobs, flags.exclude);

  // Group all program source files by their containing directory (a component),
  // restricted to the configured roots and to intra-repo files.
  const rootsAbs = flags.roots.map((r) => path.resolve(repo, r));
  const comps = new Map<string, Comp>();
  const fileToComp = new Map<string, string>(); // abs file -> component id

  for (const sf of program.getSourceFiles()) {
    const fileName = sf.fileName;
    if (isDecl(fileName)) continue;
    if (!isUnderRoots(fileName, rootsAbs)) continue;
    if (isTestFile(fileName)) continue; // tests don't create components
    const dir = path.dirname(fileName);
    const id = toRel(repo, dir);
    if (excluded(id)) continue; // -exclude: skip generated/vendored dirs entirely
    let c = comps.get(id);
    if (!c) {
      c = {
        id,
        dir,
        files: [],
        loc: 0,
        hasTests: false,
        hasDoc: false,
        synopsis: "",
        deps: new Set(),
        types: [],
        functions: [],
      };
      comps.set(id, c);
    }
    c.files.push(fileName);
    fileToComp.set(fileName, id);
  }

  // For each component, reshape exported symbols + compute health signals.
  for (const c of comps.values()) {
    extractComponent(c, program, checker, repo, rootsAbs);
  }

  // Resolve dependency edges (file imports -> component ids), intra-repo only.
  for (const c of comps.values()) {
    for (const f of c.files) {
      const sf = program.getSourceFile(f);
      if (!sf) continue;
      for (const targetFile of importedFiles(sf, program)) {
        const depId = fileToComp.get(targetFile) ?? compIdOfFile(targetFile, repo, rootsAbs, comps);
        if (depId && depId !== c.id) c.deps.add(depId);
      }
    }
  }

  const ir = buildIR(module, comps);

  switch (flags.mode) {
    case "ir":
      process.stdout.write(JSON.stringify(ir, null, 2) + "\n");
      break;
    default:
      process.stderr.write("mode must be: ir\n");
      process.exit(2);
  }
}

// ---------- program construction (the native toolchain) ----------

function createProgram(repo: string, flags: Flags): ts.Program {
  let options: ts.CompilerOptions = {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    allowJs: true,
    jsx: ts.JsxEmit.ReactJSX,
    noEmit: true,
    skipLibCheck: true,
  };
  let rootNames: string[] = [];
  let configDir = repo;

  // Prefer loading the project's tsconfig so path aliases (@/ -> src/) and the
  // exact file set resolve correctly — let the toolchain do it, never reimplement.
  if (flags.tsconfig) {
    const tsconfigPath = path.resolve(repo, flags.tsconfig);
    configDir = path.dirname(tsconfigPath);
    const parsed = ts.getParsedCommandLineOfConfigFile(
      tsconfigPath,
      {},
      {
        ...ts.sys,
        onUnRecoverableConfigFileDiagnostic: () => {},
      } as ts.ParseConfigFileHost
    );
    if (parsed) {
      options = { ...parsed.options, noEmit: true, skipLibCheck: true };
      rootNames = parsed.fileNames;
    }
  }

  // If no tsconfig (or it produced nothing), discover root files under the roots.
  if (rootNames.length === 0) {
    for (const r of flags.roots) {
      walkSource(path.resolve(repo, r), (f) => {
        if (isSource(f) || isDecl(f)) rootNames.push(f);
      });
    }
  }

  return ts.createProgram({ rootNames, options });
}

// ---------- per-component extraction ----------

function extractComponent(
  c: Comp,
  program: ts.Program,
  checker: ts.TypeChecker,
  repo: string,
  rootsAbs: string[]
) {
  // health: loc (non-blank, tests excluded — tests already excluded from files)
  for (const f of c.files) {
    c.loc += countNonBlankLines(f);
  }

  // has_tests: a sibling test file OR a __tests__/ dir alongside the component.
  c.hasTests = hasTestSibling(c.dir);

  // Per-file: collect exported types & functions, in stable (file, then name) order.
  const files = [...c.files].sort();
  for (const f of files) {
    const sf = program.getSourceFile(f);
    if (!sf) continue;
    try {
      collectExports(sf, checker, c);
    } catch {
      // fail soft: a file we cannot reshape is skipped, never crashes the run.
      continue;
    }
  }

  // synopsis + has_doc heuristic (documented in README):
  //   has_doc = the component's entry file (index.* if present, else the
  //   dir-named file, else the first file) carries a leading file-level JSDoc/
  //   block comment, OR any exported symbol in the component carries JSDoc.
  //   synopsis = first line of that leading file comment if present, else "".
  // TS has no package-doc concept, so this is a defensible proxy; NEVER invent.
  const entry = entryFile(c);
  const leading = entry ? leadingFileComment(program.getSourceFile(entry)) : "";
  const anySymbolDoc = c.types.some((t) => t.doc) || c.functions.some((fn) => fn.doc);
  c.hasDoc = leading.trim() !== "" || anySymbolDoc;
  c.synopsis = leading.trim() !== "" ? synopsisLine(leading) : "";

  // stable ordering of the surface
  c.types.sort((a, b) => a.name.localeCompare(b.name));
  c.functions.sort((a, b) => a.name.localeCompare(b.name));
}

function collectExports(sf: ts.SourceFile, checker: ts.TypeChecker, c: Comp) {
  const sym = checker.getSymbolAtLocation(sf);
  if (sym) {
    // Module has exports — enumerate them via the checker (handles `export {}`,
    // re-exports, default exports, const/arrow exports uniformly).
    for (const ex of checker.getExportsOfModule(sym)) {
      reshapeExport(ex, checker, sf, c);
    }
    return;
  }
  // No module symbol (e.g. a script file): fall back to scanning top-level
  // `export` declarations directly.
  ts.forEachChild(sf, (node) => {
    if (!hasExportModifier(node)) return;
    reshapeNode(node, checker, c);
  });
}

function reshapeExport(ex: ts.Symbol, checker: ts.TypeChecker, sf: ts.SourceFile, c: Comp) {
  let sym = ex;
  // Follow aliases (re-exports / `export { X } from "./y"`) to the real symbol.
  if (sym.flags & ts.SymbolFlags.Alias) {
    try {
      sym = checker.getAliasedSymbol(sym);
    } catch {
      // keep the alias symbol if it can't be resolved
    }
  }
  const decl = (sym.getDeclarations() ?? [])[0];
  // Only reshape symbols whose declaration lives in THIS file, so re-exports are
  // attributed to their defining component, not the re-exporter (avoids dupes).
  if (!decl || decl.getSourceFile().fileName !== sf.fileName) return;

  const name = ex.getName() === "default" ? defaultName(decl) : ex.getName();
  reshapeSymbolDecl(name, decl, sym, checker, c);
}

function reshapeNode(node: ts.Node, checker: ts.TypeChecker, c: Comp) {
  const sym = nodeSymbol(node, checker);
  const name = nodeName(node);
  if (!name) return;
  reshapeSymbolDecl(name, node, sym, checker, c);
}

function reshapeSymbolDecl(
  name: string,
  decl: ts.Node,
  sym: ts.Symbol | undefined,
  checker: ts.TypeChecker,
  c: Comp
) {
  const doc = symbolDoc(decl, sym);
  if (ts.isInterfaceDeclaration(decl)) {
    c.types.push({ name, kind: "interface", doc, members: interfaceMembers(decl, checker), methods: interfaceMethods(decl, checker) });
  } else if (ts.isClassDeclaration(decl)) {
    const { members, methods } = classShape(decl, checker);
    c.types.push({ name, kind: "class", doc, members, methods });
  } else if (ts.isEnumDeclaration(decl)) {
    c.types.push({ name, kind: "enum", doc, members: decl.members.map((m) => m.name.getText()) });
  } else if (ts.isTypeAliasDeclaration(decl)) {
    c.types.push({ name, kind: "alias", doc, members: typeAliasMembers(decl) });
  } else if (ts.isFunctionDeclaration(decl)) {
    c.functions.push({ name, signature: funcSignature(name, decl), doc });
  } else if (ts.isVariableDeclaration(decl) || ts.isVariableStatement(decl)) {
    // exported const/let — only surface it if it is a function value (arrow/fn
    // expression); plain data constants are implementation detail, omitted.
    const vd = ts.isVariableDeclaration(decl) ? decl : firstVarDecl(decl);
    if (vd && vd.initializer && (ts.isArrowFunction(vd.initializer) || ts.isFunctionExpression(vd.initializer))) {
      c.functions.push({ name, signature: arrowSignature(name, vd.initializer), doc });
    }
  }
}

// ---------- type/function reshapers (the compression: signatures, no bodies) ----------

function interfaceMembers(decl: ts.InterfaceDeclaration, checker: ts.TypeChecker): string[] {
  const out: string[] = [];
  for (const m of decl.members) {
    if (ts.isPropertySignature(m) && m.name) {
      const opt = m.questionToken ? "?" : "";
      out.push(`${m.name.getText()}${opt}: ${typeText(m.type, checker)}`);
    }
  }
  return out;
}

function interfaceMethods(decl: ts.InterfaceDeclaration, checker: ts.TypeChecker): string[] {
  const out: string[] = [];
  for (const m of decl.members) {
    if (ts.isMethodSignature(m) && m.name) {
      out.push(methodLine(m.name.getText(), m, checker));
    }
  }
  return out;
}

function classShape(decl: ts.ClassDeclaration, checker: ts.TypeChecker): { members: string[]; methods: string[] } {
  const members: string[] = [];
  const methods: string[] = [];
  for (const m of decl.members) {
    if (isPrivateMember(m)) continue;
    if (ts.isPropertyDeclaration(m) && m.name) {
      const opt = m.questionToken ? "?" : "";
      members.push(`${m.name.getText()}${opt}: ${typeText(m.type, checker)}`);
    } else if (ts.isMethodDeclaration(m) && m.name) {
      methods.push(methodLine(m.name.getText(), m, checker));
    } else if (ts.isGetAccessor(m) && m.name) {
      members.push(`get ${m.name.getText()}: ${typeText(m.type, checker)}`);
    }
  }
  return { members, methods };
}

function typeAliasMembers(decl: ts.TypeAliasDeclaration): string[] {
  // For an object-type alias, surface its property shape; otherwise the aliased
  // form (e.g. a union) as a single "= ..." line. Bodies of mapped/complex types
  // are kept compact.
  const t = decl.type;
  if (ts.isTypeLiteralNode(t)) {
    return t.members.map((m) => collapse(m.getText()));
  }
  return [`= ${collapse(t.getText())}`];
}

function funcSignature(name: string, decl: ts.FunctionDeclaration): string {
  const params = (decl.parameters ?? []).map((p) => collapse(p.getText())).join(", ");
  const ret = decl.type ? `: ${collapse(decl.type.getText())}` : "";
  const tp = typeParams(decl.typeParameters);
  return `${name}${tp}(${params})${ret}`;
}

function arrowSignature(name: string, fn: ts.ArrowFunction | ts.FunctionExpression): string {
  const params = (fn.parameters ?? []).map((p) => collapse(p.getText())).join(", ");
  const ret = fn.type ? `: ${collapse(fn.type.getText())}` : "";
  const tp = typeParams(fn.typeParameters);
  return `${name}${tp}(${params})${ret}`;
}

function methodLine(name: string, m: ts.MethodSignature | ts.MethodDeclaration, _checker: ts.TypeChecker): string {
  const params = (m.parameters ?? []).map((p) => collapse(p.getText())).join(", ");
  const ret = m.type ? `: ${collapse(m.type.getText())}` : "";
  const tp = typeParams(m.typeParameters);
  return `${name}${tp}(${params})${ret}`;
}

function typeParams(tps?: ts.NodeArray<ts.TypeParameterDeclaration>): string {
  if (!tps || tps.length === 0) return "";
  return `<${tps.map((t) => collapse(t.getText())).join(", ")}>`;
}

function typeText(node: ts.TypeNode | undefined, _checker: ts.TypeChecker): string {
  if (!node) return "unknown";
  return collapse(node.getText());
}

// ---------- imports -> dependency edges ----------

function importedFiles(sf: ts.SourceFile, program: ts.Program): string[] {
  const out: string[] = [];
  const resolved = (sf as any).resolvedModules as Map<string, any> | undefined;
  // Walk import/export-from declarations; use the program's resolved module map
  // when available (handles path aliases the toolchain already resolved).
  ts.forEachChild(sf, (node) => {
    let spec: string | undefined;
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      spec = node.moduleSpecifier.text;
    }
    if (!spec) return;
    let target: string | undefined;
    if (resolved && resolved.has(spec)) {
      target = resolved.get(spec)?.resolvedFileName;
    }
    if (!target) {
      // fall back to the program's resolver
      const rm = ts.resolveModuleName(spec, sf.fileName, program.getCompilerOptions(), ts.sys);
      target = rm.resolvedModule?.resolvedFileName;
    }
    if (target && !target.includes("node_modules")) out.push(target);
  });
  return out;
}

function compIdOfFile(file: string, repo: string, rootsAbs: string[], comps: Map<string, Comp>): string | undefined {
  if (!isUnderRoots(file, rootsAbs)) return undefined;
  const id = toRel(repo, path.dirname(file));
  return comps.has(id) ? id : undefined;
}

// ---------- IR assembly ----------

function buildIR(module: string, comps: Map<string, Comp>): IR {
  const ir: IR = { module, language: "ts", components: [] };
  const ids = [...comps.keys()].sort();
  for (const id of ids) {
    const c = comps.get(id)!;
    const deps = [...c.deps].sort();
    const comp: IRComponent = {
      id: c.id,
      name: lastSeg(c.id),
      path: c.id,
      loc: c.loc,
      kind: "module",
      synopsis: c.synopsis,
      depends_on: deps,
      has_doc: c.hasDoc,
      has_tests: c.hasTests,
    };
    if (c.types.length) comp.types = c.types;
    if (c.functions.length) comp.functions = c.functions;
    ir.components.push(comp);
  }
  return ir;
}

// ---------- doc-comment helpers ----------

function symbolDoc(decl: ts.Node, sym: ts.Symbol | undefined): string {
  if (sym) {
    const parts = sym.getDocumentationComment(undefined);
    const txt = ts.displayPartsToString(parts).trim();
    if (txt) return synopsisLine(txt);
  }
  // Fall back to the JSDoc attached to the declaration node.
  const jsdoc = (decl as any).jsDoc as ts.JSDoc[] | undefined;
  if (jsdoc && jsdoc.length) {
    const c = jsdoc[jsdoc.length - 1].comment;
    if (typeof c === "string" && c.trim()) return synopsisLine(c);
  }
  return "";
}

// leadingFileComment returns the file-level leading comment block (the first
// comment before the first statement), used as a package-doc proxy.
function leadingFileComment(sf: ts.SourceFile | undefined): string {
  if (!sf) return "";
  const text = sf.getFullText();
  const first = sf.statements[0];
  const end = first ? first.getStart(sf) : text.length;
  const ranges = ts.getLeadingCommentRanges(text, 0) ?? [];
  // require the comment to actually sit before the first statement
  const usable = ranges.filter((r) => r.end <= end);
  if (usable.length === 0) return "";
  const r = usable[0];
  return stripComment(text.slice(r.pos, r.end));
}

function stripComment(raw: string): string {
  let s = raw.trim();
  if (s.startsWith("/**")) s = s.slice(3);
  else if (s.startsWith("/*")) s = s.slice(2);
  if (s.endsWith("*/")) s = s.slice(0, -2);
  return s
    .split("\n")
    .map((l) => l.replace(/^\s*\*\s?/, "").trimEnd())
    .join("\n")
    .replace(/^\/\/+\s?/gm, "")
    .trim();
}

function synopsisLine(s: string): string {
  s = s.trim();
  if (s === "") return "";
  const nl = s.indexOf("\n");
  if (nl >= 0) s = s.slice(0, nl);
  const dot = s.indexOf(". ");
  if (dot >= 0) s = s.slice(0, dot + 1);
  return s.trim();
}

// ---------- AST shape helpers ----------

function hasExportModifier(node: ts.Node): boolean {
  const mods = (ts.canHaveModifiers(node) ? ts.getModifiers(node) : undefined) ?? [];
  return mods.some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
}

function isPrivateMember(m: ts.ClassElement): boolean {
  const mods = (ts.canHaveModifiers(m) ? ts.getModifiers(m) : undefined) ?? [];
  if (mods.some((x) => x.kind === ts.SyntaxKind.PrivateKeyword)) return true;
  if (m.name && ts.isPrivateIdentifier(m.name)) return true;
  return false;
}

function nodeSymbol(node: ts.Node, checker: ts.TypeChecker): ts.Symbol | undefined {
  const name = (node as any).name as ts.Node | undefined;
  if (name) return checker.getSymbolAtLocation(name);
  return undefined;
}

function nodeName(node: ts.Node): string | undefined {
  const n = (node as any).name;
  if (n && ts.isIdentifier(n)) return n.text;
  if (ts.isVariableStatement(node)) {
    const vd = firstVarDecl(node);
    if (vd && ts.isIdentifier(vd.name)) return vd.name.text;
  }
  return undefined;
}

function firstVarDecl(stmt: ts.VariableStatement): ts.VariableDeclaration | undefined {
  return stmt.declarationList.declarations[0];
}

function defaultName(decl: ts.Node): string {
  const n = (decl as any).name;
  if (n && ts.isIdentifier(n)) return n.text;
  return "default";
}

// ---------- filesystem / repo helpers ----------

function isUnderRoots(file: string, rootsAbs: string[]): boolean {
  const f = path.resolve(file);
  return rootsAbs.some((r) => f === r || f.startsWith(r + path.sep));
}

function toRel(repo: string, p: string): string {
  return path.relative(repo, p).split(path.sep).join("/");
}

function entryFile(c: Comp): string | undefined {
  const byName = new Map(c.files.map((f) => [path.basename(f).toLowerCase(), f]));
  for (const cand of ["index.ts", "index.tsx", "index.js", "index.jsx", "index.mjs"]) {
    if (byName.has(cand)) return byName.get(cand);
  }
  const dirName = lastSeg(c.id).toLowerCase();
  for (const ext of [".ts", ".tsx", ".js", ".jsx"]) {
    if (byName.has(dirName + ext)) return byName.get(dirName + ext);
  }
  return [...c.files].sort()[0];
}

function hasTestSibling(dir: string): boolean {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return false;
  }
  for (const e of entries) {
    if (e.isDirectory() && e.name === "__tests__") return true;
    if (e.isFile() && isTestFile(e.name)) return true;
  }
  return false;
}

function countNonBlankLines(file: string): number {
  let data: string;
  try {
    data = fs.readFileSync(file, "utf8");
  } catch {
    return 0;
  }
  let n = 0;
  for (const line of data.split("\n")) {
    if (line.trim() !== "") n++;
  }
  return n;
}

function walkSource(dir: string, cb: (f: string) => void) {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (e.name === "node_modules" || e.name.startsWith(".")) continue;
      walkSource(full, cb);
    } else if (e.isFile()) {
      cb(full);
    }
  }
}

function deriveModuleName(repo: string, tsconfig: string): string {
  const candidates: string[] = [];
  if (tsconfig) candidates.push(path.dirname(path.resolve(repo, tsconfig)));
  candidates.push(repo);
  for (const dir of candidates) {
    const pkg = path.join(dir, "package.json");
    try {
      const j = JSON.parse(fs.readFileSync(pkg, "utf8"));
      if (j.name) return String(j.name);
    } catch {
      // keep looking
    }
  }
  return path.basename(repo);
}

function collapse(s: string): string {
  return s.replace(/\s+/g, " ").trim();
}

function lastSeg(p: string): string {
  const i = p.lastIndexOf("/");
  return i >= 0 ? p.slice(i + 1) : p;
}

main();
