// Builds a run file with the browser's own code. Used by tests/test_frontend.py.
// Usage: node tests/web/export-run.mjs <state.json> [branch-id-to-delete]
import { readFileSync } from 'node:fs';
import { loadApp } from './load-app.mjs';

const [statePath, deleteBranchId] = process.argv.slice(2);
const { run, nodes } = JSON.parse(readFileSync(statePath, 'utf8'));
const { app } = loadApp();
app.state.run = run;
app.state.nodes = new Map(nodes.map(node => [node.id, node]));
if (deleteBranchId) for (const id of app.branchNodeIds(deleteBranchId)) app.state.nodes.delete(id);
process.stdout.write(JSON.stringify(app.buildRunFile()));
