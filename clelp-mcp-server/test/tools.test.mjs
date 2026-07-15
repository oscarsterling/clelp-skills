/**
 * End-to-end MCP tool tests against dist/index.js + a local mock Clelp API.
 * No network: mock is bound to 127.0.0.1; CLELP_API_URL uses http://localhost:<port>.
 */

import { test, describe, before, after, afterEach } from "node:test";
import assert from "node:assert/strict";
import { resolve } from "node:path";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { startMockApi, MOCK_SKILL } from "./mock-api.mjs";

const SERVER_ENTRY = resolve(process.cwd(), "dist/index.js");
const VALID_COMMENTARY =
  "This skill works well for reading and writing files in agent workflows.";

/**
 * Parse the first text content block of a callTool result as JSON.
 * The server always returns content: [{ type: "text", text: JSON.stringify(...) }].
 */
function parseToolJson(result) {
  assert.ok(result?.content?.length, "tool result should have content");
  const block = result.content.find((c) => c.type === "text");
  assert.ok(block, "tool result should include a text content block");
  return JSON.parse(block.text);
}

/**
 * Spawn the compiled MCP server and connect a real MCP client over stdio.
 *
 * @param {{ apiUrl: string, apiKey?: string|null, extraEnv?: Record<string,string> }} opts
 */
async function connectServer({ apiUrl, apiKey = null, extraEnv = {} }) {
  const env = {
    CLELP_API_URL: apiUrl,
    ...extraEnv,
  };
  // Only set CLELP_API_KEY when provided; omit so the server sees an empty key.
  // StdioClientTransport merges with a safe default env (PATH, HOME, …), not
  // the full parent process.env, so parent CLELP_API_KEY is not inherited.
  if (apiKey != null && apiKey !== "") {
    env.CLELP_API_KEY = apiKey;
  }

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [SERVER_ENTRY],
    env,
    stderr: "pipe",
  });

  const client = new Client({ name: "clelp-tools-test", version: "1.0.0" });
  await client.connect(transport);

  return {
    client,
    transport,
    async close() {
      try {
        await client.close();
      } catch {
        /* ignore */
      }
      try {
        await transport.close();
      } catch {
        /* ignore */
      }
    },
  };
}

describe("clelp MCP tools (e2e via stdio + mock API)", () => {
  /** @type {Awaited<ReturnType<typeof startMockApi>>} */
  let mock;
  /** @type {Awaited<ReturnType<typeof connectServer>> | null} */
  let session = null;

  before(async () => {
    mock = await startMockApi();
  });

  after(async () => {
    if (session) {
      await session.close();
      session = null;
    }
    if (mock) await mock.close();
  });

  afterEach(async () => {
    // Default: tear down per-test server so rate-limit state does not leak.
    // The rate-limit test manages its own long-lived session.
    if (session) {
      await session.close();
      session = null;
    }
  });

  test("listTools returns exactly the 3 tool names", async () => {
    session = await connectServer({ apiUrl: mock.baseUrl });
    const listed = await session.client.listTools();
    const names = (listed.tools || []).map((t) => t.name).sort();
    assert.deepEqual(names, ["clelp_get_skill", "clelp_rate", "clelp_search"]);
  });

  test("clelp_search returns skills with mapped fields", async () => {
    const requestCountBefore = mock.requests.length;
    session = await connectServer({ apiUrl: mock.baseUrl });

    const raw = await session.client.callTool({
      name: "clelp_search",
      arguments: { query: "filesystem", limit: 5 },
    });
    const result = parseToolJson(raw);

    assert.ok(Array.isArray(result.skills), "skills should be an array");
    assert.equal(result.count, result.skills.length);
    assert.equal(result.skills.length, 1);
    assert.equal(result.skills[0].name, MOCK_SKILL.name);
    assert.equal(result.skills[0].slug, MOCK_SKILL.slug);
    assert.equal(result.skills[0].avg_claws, MOCK_SKILL.avg_claws);

    const searchCalls = mock.requests
      .slice(requestCountBefore)
      .filter((r) => r.method === "GET" && r.url.startsWith("/skills?"));
    assert.ok(searchCalls.length >= 1, "mock should have received GET /skills?...");
  });

  test("clelp_get_skill happy path returns skill with id, name, tip", async () => {
    session = await connectServer({ apiUrl: mock.baseUrl });

    const raw = await session.client.callTool({
      name: "clelp_get_skill",
      arguments: { skill_id: "filesystem-mcp" },
    });
    const result = parseToolJson(raw);

    assert.equal(result.id, MOCK_SKILL.id);
    assert.equal(result.name, MOCK_SKILL.name);
    assert.ok(typeof result.tip === "string" && result.tip.length > 0, "tip present");
    assert.equal(result.error, undefined);
  });

  test("clelp_get_skill rejects invalid skill_id without calling the API", async () => {
    const requestCountBefore = mock.requests.length;
    session = await connectServer({ apiUrl: mock.baseUrl });

    // Space and "!" fail SKILL_ID_PATTERN /^[a-zA-Z0-9_@./-]{1,100}$/
    const raw = await session.client.callTool({
      name: "clelp_get_skill",
      arguments: { skill_id: "bad id!!" },
    });
    const result = parseToolJson(raw);

    assert.match(
      String(result.error || JSON.stringify(result)),
      /Invalid skill_id format/
    );

    const newRequests = mock.requests.slice(requestCountBefore);
    assert.equal(
      newRequests.length,
      0,
      "invalid skill_id must not hit the mock API"
    );
  });

  test("clelp_rate without CLELP_API_KEY returns success: false", async () => {
    session = await connectServer({ apiUrl: mock.baseUrl, apiKey: null });

    const raw = await session.client.callTool({
      name: "clelp_rate",
      arguments: {
        skill_id: "filesystem-mcp",
        claws: 5,
        commentary: VALID_COMMENTARY,
      },
    });
    const result = parseToolJson(raw);

    assert.equal(result.success, false);
    assert.match(String(result.error), /API key required/i);
  });

  test("clelp_rate with API key + valid input returns success: true", async () => {
    session = await connectServer({
      apiUrl: mock.baseUrl,
      apiKey: "test-key-valid-rate",
    });

    const raw = await session.client.callTool({
      name: "clelp_rate",
      arguments: {
        skill_id: "filesystem-mcp",
        claws: 5,
        commentary: VALID_COMMENTARY,
      },
    });
    const result = parseToolJson(raw);

    assert.equal(result.success, true);
    assert.ok(result.message);
  });

  test("clelp_rate commentary too short does not call the API", async () => {
    const requestCountBefore = mock.requests.length;
    session = await connectServer({
      apiUrl: mock.baseUrl,
      apiKey: "test-key-short-comment",
    });

    const raw = await session.client.callTool({
      name: "clelp_rate",
      arguments: {
        skill_id: "filesystem-mcp",
        claws: 4,
        commentary: "too short",
      },
    });
    const result = parseToolJson(raw);

    assert.equal(result.success, false);
    assert.match(String(result.error), /Commentary must be at least/i);

    const newRequests = mock.requests.slice(requestCountBefore);
    assert.equal(
      newRequests.length,
      0,
      "short commentary must not hit the mock API"
    );
  });

  test("clelp_rate invalid claws returns success: false", async () => {
    session = await connectServer({
      apiUrl: mock.baseUrl,
      apiKey: "test-key-bad-claws",
    });

    for (const claws of [7, 2.5, 0]) {
      const raw = await session.client.callTool({
        name: "clelp_rate",
        arguments: {
          skill_id: "filesystem-mcp",
          claws,
          commentary: VALID_COMMENTARY,
        },
      });
      const result = parseToolJson(raw);
      assert.equal(result.success, false, `claws=${claws} should fail`);
      assert.match(String(result.error), /Claws must be an integer/i);
    }
  });

  test("clelp_rate enforces 10 ratings/day (11th fails) on one server process", async () => {
    // Rate limit state is in-memory per server process. Reuse ONE connection
    // so all 11 calls share the same Map.
    session = await connectServer({
      apiUrl: mock.baseUrl,
      apiKey: "test-key-rate-limit",
    });

    for (let i = 1; i <= 10; i++) {
      const raw = await session.client.callTool({
        name: "clelp_rate",
        arguments: {
          skill_id: MOCK_SKILL.id, // UUID skips slug resolve GET
          claws: 5,
          commentary: `${VALID_COMMENTARY} (#${i})`,
        },
      });
      const result = parseToolJson(raw);
      assert.equal(result.success, true, `rating ${i} should succeed`);
    }

    const raw11 = await session.client.callTool({
      name: "clelp_rate",
      arguments: {
        skill_id: MOCK_SKILL.id,
        claws: 5,
        commentary: `${VALID_COMMENTARY} (#11 should fail)`,
      },
    });
    const result11 = parseToolJson(raw11);
    assert.equal(result11.success, false);
    assert.match(String(result11.error), /Rate limit exceeded/i);
  });
});
