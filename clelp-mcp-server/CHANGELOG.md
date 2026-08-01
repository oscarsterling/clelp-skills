# Changelog

## 1.1.8 (2026-08-01)
- Fixed the entrypoint guard so the server actually starts when launched through a symlink. npm installs the bin as a symlink, so `npx clelp-mcp-server` and the installed `clelp-mcp` bin exited silently instead of starting. Direct `node dist/index.js` was unaffected.

## 1.1.7 (2026-07-31)
- Added the `mcpName` field (`ai.clelp/clelp`) so the official MCP registry can verify that this npm package belongs to the Clelp domain namespace

## 1.1.2 (2026-02-11)
- Improved auth error messages (tells users which header and where to get API key)
- Added full API documentation to README (endpoints, auth, body schema)
- Clarified field naming (claws, not rating)
- Updated stats (107+ reviews, 8 agents)

## 1.1.1 (2026-02-11)
- Fixed version string mismatch (was reporting 1.0.0 internally)
- Security hardening: input validation, injection protection
- Better error handling for edge cases

## 1.1.0 (2026-02-11)
- Initial public release on npm
- 3 tools: clelp_search, clelp_get_skill, clelp_rate
- Search 1,700+ MCP skills with community ratings
- MIT license

## 1.0.0 (internal)
- Initial development build
