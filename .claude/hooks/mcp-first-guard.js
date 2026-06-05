// MCP-first guardrail -- PreToolUse(Grep) hook.
// Cross-platform: node ships with Claude Code, so this runs on macOS / Linux / Windows alike.
// Injects a reminder as additionalContext on every Grep; never blocks (grep stays usable for the 4 fallbacks).
const ctx =
  '[MCP-first guardrail] Locate/analyze code -> query platform MCP FIRST, do not grep-first. ' +
  'symbols/defs/call-chains/impact: codegraph (codegraph_search/context/callers/callees/impact/node). ' +
  'cross-layer/table-usage/endpoint-callers/page-deps/business-domain: graph (find_table_usage/find_api_callers/find_impact/find_node_domain/search_nodes). ' +
  'rules/design/incident docs: platform-docs (search_docs/get_by_file). ' +
  'grep+Read ONLY for 4 fallbacks: dirty worktree in query scope / stale index / MCP unavailable / verify real line numbers after edits. ' +
  'Confirm this grep is a fallback, else switch to MCP.';
process.stdout.write(
  JSON.stringify({ hookSpecificOutput: { hookEventName: 'PreToolUse', additionalContext: ctx } }),
);
