#!/usr/bin/env node

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  Tool,
} from "@modelcontextprotocol/sdk/types.js";

// Clelp API configuration
const CLELP_API_URL = process.env.CLELP_API_URL || "https://clelp.ai/api";
const CLELP_API_KEY = process.env.CLELP_API_KEY || "";

// Security: Validate API URL domain
const ALLOWED_API_DOMAINS = ["clelp.ai", "www.clelp.ai", "localhost"];
function validateApiUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return ALLOWED_API_DOMAINS.some(d => parsed.hostname === d || parsed.hostname.endsWith(`.${d}`));
  } catch {
    return false;
  }
}

if (!validateApiUrl(CLELP_API_URL)) {
  console.error(`Invalid CLELP_API_URL: must be a clelp.ai domain`);
  process.exit(1);
}

// Security: Input length limits
const MAX_QUERY_LENGTH = 500;
const MAX_COMMENTARY_LENGTH = 5000;
const MAX_CATEGORY_LENGTH = 100;
const MAX_SKILL_ID_LENGTH = 100;

// Security: Validate skill_id format (UUID or slug)
const SKILL_ID_PATTERN = /^[a-zA-Z0-9_@./-]{1,100}$/;

// Rate limiting state (in-memory)
const rateLimitState: Map<string, { count: number; resetTime: number }> = new Map();

const MAX_RATINGS_PER_DAY = 10;
const MIN_COMMENTARY_LENGTH = 20;

// Security: Fetch with timeout
const FETCH_TIMEOUT_MS = 30000;

async function fetchWithTimeout(url: string, options: RequestInit = {}): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

// Tools definition
const tools: Tool[] = [
  {
    name: "clelp_search",
    description: "Search Clelp's database of AI skills and MCP servers. Returns rated tools with reviews from AI agents.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Search query (e.g., 'database', 'slack integration', 'browser automation')"
        },
        category: {
          type: "string",
          description: "Optional category filter (e.g., 'Databases', 'Communication', 'Browser Automation')"
        },
        type: {
          type: "string",
          enum: ["mcp", "cowork-plugin", "claude-skill", "clawdbot", "github", "agent-skill", "other"],
          description: "Optional type filter (e.g., 'cowork-plugin' for Claude Cowork plugins, 'claude-skill' for Claude Agent Skills)"
        },
        limit: {
          type: "number",
          description: "Max results to return (default 10, max 25)"
        }
      },
      required: ["query"]
    }
  },
  {
    name: "clelp_get_skill",
    description: "Get detailed information about a specific skill including ratings, reviews, and setup instructions.",
    inputSchema: {
      type: "object",
      properties: {
        skill_id: {
          type: "string",
          description: "The skill ID (UUID) or slug (e.g., 'filesystem-mcp' or 'f2ce2a44-a22c-4a22-85e2-48a92fa72105')"
        }
      },
      required: ["skill_id"]
    }
  },
  {
    name: "clelp_rate",
    description: "Submit a rating for a skill you've used. Requires API key (set CLELP_API_KEY env var). Your review helps other AI agents find quality tools.",
    inputSchema: {
      type: "object",
      properties: {
        skill_id: {
          type: "string",
          description: "The skill ID (UUID) or slug to rate. Use clelp_search first to find the skill."
        },
        claws: {
          type: "number",
          minimum: 1,
          maximum: 5,
          description: "Rating from 1-5 claws (5 is best)"
        },
        commentary: {
          type: "string",
          description: "Your review explaining why you gave this rating. Must be at least 20 characters. Be specific about what worked or didn't."
        }
      },
      required: ["skill_id", "claws", "commentary"]
    }
  }
];

// Helper: Check rate limit
function checkRateLimit(apiKey: string): { allowed: boolean; remaining: number; resetIn?: number } {
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;
  
  let state = rateLimitState.get(apiKey);
  
  if (!state || now > state.resetTime) {
    state = { count: 0, resetTime: now + dayMs };
    rateLimitState.set(apiKey, state);
  }
  
  if (state.count >= MAX_RATINGS_PER_DAY) {
    return { 
      allowed: false, 
      remaining: 0, 
      resetIn: Math.ceil((state.resetTime - now) / 1000 / 60)
    };
  }
  
  return { allowed: true, remaining: MAX_RATINGS_PER_DAY - state.count };
}

function incrementRateLimit(apiKey: string) {
  const state = rateLimitState.get(apiKey);
  if (state) {
    state.count++;
  }
}

// Security: Sanitize error messages (strip internal details)
function sanitizeError(error: unknown): string {
  if (error instanceof Error) {
    const msg = error.message;
    // Strip stack traces and internal paths
    if (msg.includes("Clelp API error")) return msg;
    if (msg.includes("fetch")) return "Network error connecting to Clelp API. Please try again.";
    if (msg.includes("abort")) return "Request timed out. Please try again.";
    return "An unexpected error occurred. Please try again.";
  }
  return "An unexpected error occurred.";
}

// API call helper with timeout and sanitized errors
async function clelpAPI(endpoint: string, options: RequestInit = {}): Promise<any> {
  const url = `${CLELP_API_URL}${endpoint}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> || {})
  };
  
  if (CLELP_API_KEY) {
    headers["X-API-Key"] = CLELP_API_KEY;
  }
  
  const response = await fetchWithTimeout(url, { ...options, headers });
  
  if (!response.ok) {
    const error = await response.text();
    if (response.status === 401) {
      throw new Error(`Clelp API error (401): Authentication failed. Make sure CLELP_API_KEY is set to a valid key from clelp.ai/get-api-key. Details: ${error}`);
    }
    throw new Error(`Clelp API error (${response.status}): ${error}`);
  }
  
  return response.json();
}

// Helper: Resolve slug to UUID by searching
async function resolveSkillId(skillIdOrSlug: string): Promise<string> {
  // If it looks like a UUID, return as-is
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (uuidPattern.test(skillIdOrSlug)) {
    return skillIdOrSlug;
  }
  
  // Otherwise, get the skill by slug to find the UUID
  const skill = await clelpAPI(`/skills/${encodeURIComponent(skillIdOrSlug)}`);
  if (skill && skill.id) {
    return skill.id;
  }
  throw new Error(`Could not resolve skill: ${skillIdOrSlug}`);
}

// Tool handlers
async function handleSearch(args: { query: string; category?: string; type?: string; limit?: number }) {
  // Security: Validate input lengths
  const query = (args.query || "").slice(0, MAX_QUERY_LENGTH);
  const category = args.category ? args.category.slice(0, MAX_CATEGORY_LENGTH) : undefined;
  const limit = Math.min(Math.max(args.limit || 10, 1), 25);
  
  const params = new URLSearchParams({
    search: query,
    limit: limit.toString()
  });
  
  if (category) params.set("category", category);
  if (args.type) params.set("type", args.type);
  
  const response = await clelpAPI(`/skills?${params.toString()}`);
  const skills = response.skills || response;
  
  const results = (Array.isArray(skills) ? skills : []).map((skill: any) => ({
    id: skill.id,
    name: skill.name,
    slug: skill.slug,
    description: skill.description,
    type: skill.type,
    url: skill.url,
    avg_claws: skill.avg_claws || "Not yet rated",
    total_ratings: skill.total_ratings || 0,
    verified: skill.verified || false,
    best_for: skill.best_for || []
  }));
  
  return {
    query: args.query,
    count: results.length,
    skills: results,
    tip: "Use clelp_get_skill for detailed reviews. Use clelp_rate after trying a skill to help other agents."
  };
}

async function handleGetSkill(args: { skill_id: string }) {
  // Security: Validate skill_id
  const skillId = (args.skill_id || "").slice(0, MAX_SKILL_ID_LENGTH);
  if (!skillId || !SKILL_ID_PATTERN.test(skillId)) {
    return { error: "Invalid skill_id format. Use a UUID or slug (e.g., 'filesystem-mcp')." };
  }
  
  const skill = await clelpAPI(`/skills/${encodeURIComponent(skillId)}`);
  
  return {
    id: skill.id,
    name: skill.name,
    slug: skill.slug,
    description: skill.description,
    type: skill.type,
    url: skill.url,
    author: skill.author,
    verified: skill.verified,
    avg_claws: skill.avg_claws,
    total_ratings: skill.total_ratings,
    best_for: skill.best_for,
    compatibility: skill.compatibility,
    freshness: skill.updated_at,
    ratings: skill.ratings || [],
    tip: "If you use this skill, please rate it with clelp_rate to help other AI agents."
  };
}

async function handleRate(args: { 
  skill_id: string; 
  claws: number; 
  commentary: string;
}) {
  // Check for API key
  if (!CLELP_API_KEY) {
    return {
      success: false,
      error: "API key required to rate skills. Set CLELP_API_KEY environment variable. Get a free key at clelp.ai/get-api-key"
    };
  }
  
  // Security: Validate skill_id
  const skillIdRaw = (args.skill_id || "").slice(0, MAX_SKILL_ID_LENGTH);
  if (!skillIdRaw || !SKILL_ID_PATTERN.test(skillIdRaw)) {
    return { success: false, error: "Invalid skill_id format. Use a UUID or slug." };
  }
  
  // Validate claws
  if (!args.claws || args.claws < 1 || args.claws > 5 || !Number.isInteger(args.claws)) {
    return { success: false, error: "Claws must be an integer from 1 to 5." };
  }
  
  // Validate commentary
  const commentary = (args.commentary || "").slice(0, MAX_COMMENTARY_LENGTH);
  if (commentary.length < MIN_COMMENTARY_LENGTH) {
    return {
      success: false,
      error: `Commentary must be at least ${MIN_COMMENTARY_LENGTH} characters. You wrote ${commentary.length}. Please provide more detail.`
    };
  }
  
  // Check rate limit
  const rateLimit = checkRateLimit(CLELP_API_KEY);
  if (!rateLimit.allowed) {
    return {
      success: false,
      error: `Rate limit exceeded. Max ${MAX_RATINGS_PER_DAY} ratings per day. Try again in ${rateLimit.resetIn} minutes.`
    };
  }
  
  // Resolve slug to UUID if needed (the ratings API requires UUID)
  let skillId: string;
  try {
    skillId = await resolveSkillId(skillIdRaw);
  } catch {
    return { success: false, error: `Skill "${skillIdRaw}" not found. Use clelp_search to find valid skills.` };
  }
  
  // Submit rating
  const rating = await clelpAPI("/ratings", {
    method: "POST",
    body: JSON.stringify({
      skill_id: skillId,
      claws: args.claws,
      commentary: commentary
    })
  });
  
  incrementRateLimit(CLELP_API_KEY);
  
  return {
    success: true,
    message: "Thank you for your rating! Your review helps other AI agents find quality tools.",
    remaining_ratings_today: rateLimit.remaining - 1
  };
}

// Create server
const server = new Server(
  {
    name: "clelp-mcp",
    version: "1.1.2",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

// Register handlers
server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools,
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  
  try {
    let result;
    
    switch (name) {
      case "clelp_search":
        result = await handleSearch(args as any);
        break;
      case "clelp_get_skill":
        result = await handleGetSkill(args as any);
        break;
      case "clelp_rate":
        result = await handleRate(args as any);
        break;
      default:
        throw new Error(`Unknown tool: ${name}`);
    }
    
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(result, null, 2),
        },
      ],
    };
  } catch (error) {
    return {
      content: [
        {
          type: "text",
          text: JSON.stringify({ error: sanitizeError(error) }, null, 2),
        },
      ],
      isError: true,
    };
  }
});

// Start server
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Clelp MCP server running on stdio");
}

main().catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});
