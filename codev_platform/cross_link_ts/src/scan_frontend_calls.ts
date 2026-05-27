/**
 * M5: Scan frontend .tsx/.ts business files for calls into services/apis/* (generated API client).
 *
 * Usage:
 *   node dist/scan_frontend_calls.js <repo-root>
 *
 * Output: one JSON record per line on stdout:
 *   { caller_file, caller_symbol, caller_line, callee_name, callee_module, callee_resolved_path }
 *
 * - caller_file:           repo-relative posix path of the .tsx/.ts file containing the call
 * - caller_symbol:         enclosing function/arrow/method/class name; falls back to file basename
 * - caller_line:           1-based line of the CallExpression
 * - callee_name:           imported identifier being called (e.g. postLatestPage)
 * - callee_module:         the original import specifier (e.g. @/services/apis/configapi)
 * - callee_resolved_path:  repo-relative posix path of the resolved .ts file under services/apis/
 *
 * Excludes:
 *   - apps/stock-admin-web/src/services/apis/**  (generated client, not business code)
 *   - node_modules / dist / .umi / build
 *   - dynamic imports & re-exports are not followed (rare in business code)
 */
import {
  Project,
  SyntaxKind,
  CallExpression,
  Node,
  SourceFile,
  ScriptKind,
} from 'ts-morph';
import * as path from 'path';
import * as fs from 'fs';

interface CallRecord {
  caller_file: string;
  caller_symbol: string;
  caller_line: number;
  callee_name: string;
  callee_module: string;
  callee_resolved_path: string;
}

const SERVICES_APIS_PREFIX = 'apps/stock-admin-web/src/services/apis/';
const FRONTEND_SRC = 'apps/stock-admin-web/src';
const ALIAS_AT = '@/';

function toPosix(p: string): string {
  return p.split(path.sep).join('/');
}

function relToRepo(repoRoot: string, abs: string): string {
  return toPosix(path.relative(repoRoot, abs));
}

/**
 * Resolve module specifier to a repo-relative .ts file path under services/apis/.
 * Only resolves `@/services/apis/...`-style imports; returns null otherwise.
 */
function resolveApiModule(
  repoRoot: string,
  fromFileAbs: string,
  specifier: string,
): string | null {
  let relUnderSrc: string | null = null;

  if (specifier.startsWith(ALIAS_AT)) {
    // @/services/apis/xxx -> apps/stock-admin-web/src/services/apis/xxx
    const sub = specifier.slice(ALIAS_AT.length);
    if (!sub.startsWith('services/apis/')) {
      return null;
    }
    relUnderSrc = sub; // services/apis/xxx
  } else if (specifier.startsWith('.')) {
    // relative path — resolve against fromFileAbs, must land under services/apis/
    const resolved = path.resolve(path.dirname(fromFileAbs), specifier);
    const relRepo = toPosix(path.relative(repoRoot, resolved));
    if (!relRepo.startsWith(SERVICES_APIS_PREFIX)) {
      return null;
    }
    relUnderSrc = relRepo.slice(FRONTEND_SRC.length + 1); // strip "apps/stock-admin-web/src/"
  } else {
    return null;
  }

  // Try .ts then .tsx then index.ts
  const baseAbs = path.join(repoRoot, FRONTEND_SRC, relUnderSrc);
  const candidates = [
    baseAbs + '.ts',
    baseAbs + '.tsx',
    path.join(baseAbs, 'index.ts'),
    path.join(baseAbs, 'index.tsx'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) {
      return relToRepo(repoRoot, c);
    }
  }
  // Fallback: assume .ts even if not on disk (won't typically happen)
  return relToRepo(repoRoot, baseAbs + '.ts');
}

/**
 * Find the nearest enclosing named scope (function/arrow assigned to var/method/class/file).
 */
function enclosingSymbol(node: Node, fallbackBasename: string): string {
  let cur: Node | undefined = node.getParent();
  while (cur) {
    const k = cur.getKind();
    if (k === SyntaxKind.FunctionDeclaration) {
      const name = (cur as any).getName?.();
      if (name) return name;
    } else if (k === SyntaxKind.MethodDeclaration) {
      const name = (cur as any).getName?.();
      if (name) return name;
    } else if (k === SyntaxKind.ClassDeclaration) {
      const name = (cur as any).getName?.();
      if (name) return name;
    } else if (
      k === SyntaxKind.ArrowFunction ||
      k === SyntaxKind.FunctionExpression
    ) {
      // Try variable declaration parent
      const vd = cur.getFirstAncestorByKind(SyntaxKind.VariableDeclaration);
      if (vd) {
        const name = vd.getName();
        if (name) return name;
      }
      // Try property assignment parent
      const pa = cur.getFirstAncestorByKind(SyntaxKind.PropertyAssignment);
      if (pa) {
        const name = (pa as any).getName?.();
        if (name) return name;
      }
    }
    cur = cur.getParent();
  }
  return fallbackBasename;
}

function processSourceFile(
  repoRoot: string,
  sf: SourceFile,
  emit: (rec: CallRecord) => void,
): void {
  const filePathAbs = sf.getFilePath();
  const fileRel = relToRepo(repoRoot, filePathAbs);
  const basename = path.basename(filePathAbs).replace(/\.(tsx?|jsx?)$/, '');

  // 1) Collect imports {importedName -> {module, resolved}}
  const apiImports = new Map<string, { module: string; resolved: string }>();
  for (const imp of sf.getImportDeclarations()) {
    const spec = imp.getModuleSpecifierValue();
    const resolved = resolveApiModule(repoRoot, filePathAbs, spec);
    if (!resolved) continue;
    for (const named of imp.getNamedImports()) {
      // import { x as y } — call site uses y; track y -> x's resolved file & module
      const local = named.getAliasNode()?.getText() ?? named.getName();
      apiImports.set(local, { module: spec, resolved });
    }
  }
  if (apiImports.size === 0) return;

  // 2) Walk CallExpressions, match expression text to an imported name
  for (const call of sf.getDescendantsOfKind(SyntaxKind.CallExpression)) {
    const expr = call.getExpression();
    // Only top-level identifiers (foo(...)), not member calls (obj.foo(...))
    if (expr.getKind() !== SyntaxKind.Identifier) continue;
    const name = expr.getText();
    const hit = apiImports.get(name);
    if (!hit) continue;

    const line = call.getStartLineNumber();
    const sym = enclosingSymbol(call, basename);
    emit({
      caller_file: fileRel,
      caller_symbol: sym,
      caller_line: line,
      callee_name: name,
      callee_module: hit.module,
      callee_resolved_path: hit.resolved,
    });
  }
}

function isExcluded(relPath: string): boolean {
  if (relPath.startsWith(SERVICES_APIS_PREFIX)) return true;
  if (relPath.includes('/node_modules/')) return true;
  if (relPath.includes('/.umi/') || relPath.includes('/.umi-test/')) return true;
  if (relPath.includes('/dist/') || relPath.includes('/build/')) return true;
  return false;
}

// Swallow EPIPE when stdout consumer closes early (e.g. piped to `head`).
process.stdout.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EPIPE') process.exit(0);
  throw err;
});

function main(): void {
  const repoRoot = path.resolve(process.argv[2] || process.cwd());
  const srcRoot = path.join(repoRoot, FRONTEND_SRC);
  if (!fs.existsSync(srcRoot)) {
    console.error(`[scan_frontend_calls] frontend src not found: ${srcRoot}`);
    process.exit(2);
  }

  const project = new Project({
    useInMemoryFileSystem: false,
    skipAddingFilesFromTsConfig: true,
    skipFileDependencyResolution: true,
    compilerOptions: {
      allowJs: false,
      jsx: 4 as any, // ReactJSX
      target: 99 as any,
      module: 99 as any,
      moduleResolution: 2 as any,
      esModuleInterop: true,
      skipLibCheck: true,
      noResolve: true,
      isolatedModules: true,
    },
  });

  // Add only .ts/.tsx under src/, excluding services/apis/**, .umi, node_modules
  const patterns = [
    toPosix(path.join(srcRoot, '**/*.ts')),
    toPosix(path.join(srcRoot, '**/*.tsx')),
  ];
  const ignore = [
    toPosix(path.join(srcRoot, 'services/apis/**')),
    toPosix(path.join(srcRoot, '.umi/**')),
    toPosix(path.join(srcRoot, '.umi-test/**')),
    toPosix(path.join(srcRoot, '**/node_modules/**')),
    toPosix(path.join(srcRoot, '**/*.d.ts')),
  ];
  const added = project.addSourceFilesAtPaths([...patterns, ...ignore.map((p) => '!' + p)]);
  console.error(`[scan_frontend_calls] loaded ${added.length} source files`);

  let count = 0;
  const emit = (rec: CallRecord) => {
    process.stdout.write(JSON.stringify(rec) + '\n');
    count++;
  };

  for (const sf of added) {
    const rel = relToRepo(repoRoot, sf.getFilePath());
    if (isExcluded(rel)) continue;
    try {
      processSourceFile(repoRoot, sf, emit);
    } catch (err) {
      console.error(`[scan_frontend_calls] error in ${rel}: ${(err as Error).message}`);
    }
  }
  console.error(`[scan_frontend_calls] emitted ${count} call records`);
}

main();
