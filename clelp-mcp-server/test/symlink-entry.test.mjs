/**
 * Regression: entrypoint must run when launched via a symlink (npm bin layout).
 *
 * npm installs the package bin as a symlink:
 *   node_modules/.bin/clelp-mcp -> ../clelp-mcp-server/dist/index.js
 * process.argv[1] is the symlink path; import.meta.url is the real path.
 * A naive argv[1] === import.meta.url check skips main() and exits 0 silently.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  symlinkSync,
  rmSync,
  realpathSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve } from "node:path";

const SERVER_ENTRY = resolve(process.cwd(), "dist/index.js");
const EXPECTED_TOOLS = ["clelp_get_skill", "clelp_rate", "clelp_search"];
const TIMEOUT_MS = 10000;

/**
 * Spawn node with entryPath as argv[1], send MCP handshake + tools/list over
 * newline-delimited JSON-RPC, keep stdin open, collect until id 1 and 2 reply.
 *
 * @param {string} entryPath absolute path passed as process.argv[1]
 * @returns {Promise<{ initialize: object, toolsList: object, stdout: string, stderr: string }>}
 */
function runViaSymlink(entryPath) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(process.execPath, [entryPath], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, CLELP_API_URL: "http://localhost:9" },
    });

    let stdout = "";
    let stderr = "";
    /** @type {object | null} */
    let initialize = null;
    /** @type {object | null} */
    let toolsList = null;
    let settled = false;

    const timer = setTimeout(() => {
      finish(
        new assert.AssertionError({
          message:
            `timed out after ${TIMEOUT_MS}ms waiting for initialize (id:1) and tools/list (id:2) responses.\n` +
            `stdout:\n${stdout}\n` +
            `stderr:\n${stderr}`,
        })
      );
    }, TIMEOUT_MS);

    function finish(err) {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        child.kill("SIGKILL");
      } catch {
        // ignore
      }
      if (err) {
        reject(err);
      } else {
        resolvePromise({ initialize, toolsList, stdout, stderr });
      }
    }

    function tryParseLines() {
      const lines = stdout.split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let msg;
        try {
          msg = JSON.parse(trimmed);
        } catch {
          continue;
        }
        if (msg.id === 1) initialize = msg;
        if (msg.id === 2) toolsList = msg;
      }
      if (initialize && toolsList) {
        finish(null);
      }
    }

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      tryParseLines();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", (err) => finish(err));
    child.on("exit", (code, signal) => {
      if (settled) return;
      // Early exit without both responses is a failure (the silent-exit bug).
      if (!initialize || !toolsList) {
        finish(
          new assert.AssertionError({
            message:
              `child exited early (code=${code}, signal=${signal}) before both JSON-RPC responses arrived.\n` +
              `stdout:\n${stdout}\n` +
              `stderr:\n${stderr}`,
          })
        );
      }
    });

    // Keep stdin open (do not end it) so the server does not exit early.
    const initReq = {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "symlink-entry-test", version: "1.0.0" },
      },
    };
    const initializedNote = {
      jsonrpc: "2.0",
      method: "notifications/initialized",
    };
    const listReq = {
      jsonrpc: "2.0",
      id: 2,
      method: "tools/list",
      params: {},
    };
    child.stdin.write(JSON.stringify(initReq) + "\n");
    child.stdin.write(JSON.stringify(initializedNote) + "\n");
    child.stdin.write(JSON.stringify(listReq) + "\n");
  });
}

/**
 * Assert initialize + tools/list payloads from a successful runViaSymlink call.
 * @param {{ initialize: object, toolsList: object }} result
 */
function assertHandshake(result) {
  const serverInfo = result.initialize?.result?.serverInfo;
  assert.ok(serverInfo, "initialize result should include serverInfo");
  assert.equal(serverInfo.name, "clelp-mcp", "serverInfo.name should be clelp-mcp");

  const tools = result.toolsList?.result?.tools;
  assert.ok(Array.isArray(tools), "tools/list result should include tools array");
  const names = tools.map((t) => t.name).sort();
  assert.deepEqual(
    names,
    EXPECTED_TOOLS,
    "tools/list should return exactly the three clelp tools"
  );
}

test("CASE A: direct symlink to absolute dist/index.js runs main()", async () => {
  const realEntry = realpathSync(SERVER_ENTRY);
  const tempDir = mkdtempSync(join(tmpdir(), "clelp-symlink-a-"));
  try {
    const linkPath = join(tempDir, "clelp-mcp");
    symlinkSync(realEntry, linkPath);
    const result = await runViaSymlink(linkPath);
    assertHandshake(result);
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
});

test("CASE B: npm .bin relative symlink layout runs main()", async () => {
  // Reproduce: node_modules/.bin/clelp-mcp -> ../clelp-mcp-server/dist/index.js
  // Target is a relative path (same shape npm uses).
  const realEntry = realpathSync(SERVER_ENTRY);
  const tempDir = mkdtempSync(join(tmpdir(), "clelp-symlink-b-"));
  try {
    const binDir = join(tempDir, "node_modules", ".bin");
    const packageDir = join(tempDir, "node_modules", "clelp-mcp-server");
    const distDir = join(packageDir, "dist");
    mkdirSync(distDir, { recursive: true });
    mkdirSync(binDir, { recursive: true });

    // Place a real file (or symlink) at the package dist path so the relative
    // bin symlink resolves to an existing file under the temp tree.
    const packagedEntry = join(distDir, "index.js");
    symlinkSync(realEntry, packagedEntry);

    const linkPath = join(binDir, "clelp-mcp");
    // Relative target as npm writes it: from .bin to package dist.
    const relativeTarget = relative(binDir, packagedEntry);
    symlinkSync(relativeTarget, linkPath);

    const result = await runViaSymlink(linkPath);
    assertHandshake(result);
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
});
