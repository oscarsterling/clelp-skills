/**
 * Tiny mock Clelp HTTP API for MCP server integration tests.
 *
 * Bind to 127.0.0.1 (loopback) but call sites must pass CLELP_API_URL as
 * http://localhost:<port> so the server's domain allow-list accepts it
 * (allow-list includes "localhost", not "127.0.0.1").
 */

import http from "node:http";

/** Canned skill used by default GET handlers. */
export const MOCK_SKILL = {
  id: "f2ce2a44-a22c-4a22-85e2-48a92fa72105",
  name: "Filesystem MCP",
  slug: "filesystem-mcp",
  description: "Read and write files",
  type: "mcp",
  url: "https://example.com",
  avg_claws: 4.5,
  total_ratings: 12,
  verified: true,
  best_for: ["file ops"],
};

/**
 * Start a mock API on an ephemeral port.
 *
 * @param {Record<string, Function>} [handlers]
 *   Optional overrides keyed by `"METHOD path"` (exact path, no query),
 *   e.g. `"GET /skills"`, `"POST /ratings"`.
 *   Handler signature: (req, res, { url, path, body, send }) => void | Promise
 *   where send(status, payload) writes JSON and ends the response.
 * @returns {Promise<{
 *   port: number,
 *   baseUrl: string,
 *   requests: Array<{ method: string, url: string, body: string|null }>,
 *   close: () => Promise<void>
 * }>}
 */
export async function startMockApi(handlers = {}) {
  const requests = [];

  const server = http.createServer(async (req, res) => {
    const method = req.method || "GET";
    const rawUrl = req.url || "/";
    const pathOnly = rawUrl.split("?")[0];

    let body = null;
    if (method === "POST" || method === "PUT" || method === "PATCH") {
      body = await readBody(req);
    }

    requests.push({ method, url: rawUrl, body });

    const send = (status, payload) => {
      const json = typeof payload === "string" ? payload : JSON.stringify(payload);
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(json);
    };

    try {
      // Exact path override (e.g. "GET /skills", "POST /ratings")
      const exactKey = `${method} ${pathOnly}`;
      if (typeof handlers[exactKey] === "function") {
        await handlers[exactKey](req, res, { url: rawUrl, path: pathOnly, body, send });
        if (!res.writableEnded) {
          // Handler did not write a response; fall through is unexpected — 500.
          send(500, { error: "Handler did not send a response" });
        }
        return;
      }

      // Prefix override for skill detail: "GET /skills/" matches /skills/<id>
      if (method === "GET" && pathOnly.startsWith("/skills/")) {
        const prefixKey = "GET /skills/";
        if (typeof handlers[prefixKey] === "function") {
          await handlers[prefixKey](req, res, { url: rawUrl, path: pathOnly, body, send });
          if (!res.writableEnded) {
            send(500, { error: "Handler did not send a response" });
          }
          return;
        }
      }

      // Default routes matching what the MCP server calls
      if (method === "GET" && pathOnly === "/skills") {
        send(200, { skills: [MOCK_SKILL] });
        return;
      }

      if (method === "GET" && pathOnly.startsWith("/skills/")) {
        const idOrSlug = decodeURIComponent(pathOnly.slice("/skills/".length));
        if (idOrSlug === MOCK_SKILL.slug || idOrSlug === MOCK_SKILL.id) {
          send(200, MOCK_SKILL);
          return;
        }
        send(404, { error: "Skill not found" });
        return;
      }

      if (method === "POST" && pathOnly === "/ratings") {
        let parsed = {};
        try {
          parsed = body ? JSON.parse(body) : {};
        } catch {
          parsed = {};
        }
        send(200, {
          id: "rating-1",
          skill_id: parsed.skill_id ?? MOCK_SKILL.id,
          claws: parsed.claws ?? 5,
        });
        return;
      }

      send(404, { error: `No mock handler for ${method} ${pathOnly}` });
    } catch (err) {
      if (!res.writableEnded) {
        send(500, { error: String(err) });
      }
    }
  });

  // Bind to loopback only; tests use http://localhost:<port> for allow-list.
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve());
  });

  const addr = server.address();
  const port = typeof addr === "object" && addr ? addr.port : 0;

  return {
    port,
    /** Use hostname "localhost" so CLELP_API_URL domain validation passes. */
    baseUrl: `http://localhost:${port}`,
    requests,
    close: () =>
      new Promise((resolve, reject) => {
        server.close((err) => (err ? reject(err) : resolve()));
      }),
  };
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}
