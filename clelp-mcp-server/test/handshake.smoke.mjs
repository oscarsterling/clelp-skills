/**
 * Live MCP handshake smoke test.
 *
 * Runnable two ways:
 *   - Standalone / cron: `node test/handshake.smoke.mjs`  (or `npm run smoke`)
 *     prints PASS/FAIL and exits 0/1.
 *   - Under the test runner: `node --test test/` picks up the test() block.
 *
 * Handshake + listTools do not call the HTTP API, so no mock is required.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const EXPECTED_TOOLS = ["clelp_search", "clelp_get_skill", "clelp_rate"];
const SERVER_ENTRY = resolve(process.cwd(), "dist/index.js");

/**
 * Connect to the built MCP server over stdio, assert serverInfo + tools.
 * @returns {Promise<{ serverName: string, toolNames: string[] }>}
 */
export async function runHandshake() {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_ENTRY],
    // Point at a dummy localhost URL so domain validation passes even if
    // CLELP_API_URL is set in the parent environment to something else.
    env: {
      CLELP_API_URL: "http://localhost:9",
    },
    stderr: "pipe",
  });

  const client = new Client({ name: "clelp-handshake-smoke", version: "1.0.0" });

  try {
    await client.connect(transport);

    const serverInfo = client.getServerVersion();
    assert.ok(serverInfo, "serverInfo should be set after connect()");
    assert.equal(serverInfo.name, "clelp-mcp", "server name should be clelp-mcp");

    const listed = await client.listTools();
    const toolNames = (listed.tools || []).map((t) => t.name).sort();
    const expected = [...EXPECTED_TOOLS].sort();
    assert.deepEqual(toolNames, expected, "listTools should return the 3 clelp tools");

    return { serverName: serverInfo.name, toolNames };
  } finally {
    try {
      await client.close();
    } catch {
      // ignore close errors
    }
    try {
      await transport.close();
    } catch {
      // ignore close errors
    }
  }
}

// Detect how this file is being loaded so we only run the handshake once.
// - `node --test …` sets NODE_TEST_CONTEXT / --test; use test() only.
// - `node test/handshake.smoke.mjs` (cron/human): run once and exit 0/1.
// Note: Node may auto-execute test() even without --test, so do not register
// test() in standalone mode (would double-run the server spawn).
const isMain =
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
const underTestRunner =
  Boolean(process.env.NODE_TEST_CONTEXT) ||
  process.execArgv.some((a) => a === "--test" || a.startsWith("--test="));

if (underTestRunner) {
  test("MCP handshake + listTools smoke", async () => {
    const result = await runHandshake();
    assert.equal(result.serverName, "clelp-mcp");
    assert.equal(result.toolNames.length, 3);
  });
} else if (isMain) {
  runHandshake()
    .then((result) => {
      console.log(
        `PASS: MCP handshake ok (server=${result.serverName}, tools=${result.toolNames.join(",")})`
      );
      process.exit(0);
    })
    .catch((err) => {
      console.error("FAIL: MCP handshake smoke failed");
      console.error(err);
      process.exit(1);
    });
}
