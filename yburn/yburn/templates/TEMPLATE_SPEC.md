# Yburn Template Specification v1.0

## Overview
Each template is a directory under `yburn/templates/` containing files that define
a standalone replacement script for a mechanical cron job.

## Directory Structure
```
templates/
  <template-name>/
    manifest.json      # Template metadata and parameter definitions
    script.py          # The standalone Python script (Jinja2 template)
    example-output.txt # Sample output message
    README.md          # Human-readable description
```

## manifest.json Schema

```json
{
  "name": "template-name",
  "description": "What this template does",
  "version": "1.0",
  "match_keywords": ["keyword1", "keyword2"],
  "replaces_patterns": ["pattern in job name or payload that this replaces"],
  "parameters": [
    {
      "name": "param_name",
      "type": "str | int | float | bool | list",
      "required": true,
      "default": null,
      "description": "What this parameter controls",
      "env_var": "OPTIONAL_ENV_VAR_NAME"
    }
  ],
  "output_format": "telegram_markdown",
  "requires": ["requests"]
}
```

### Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | str | yes | Template identifier (matches directory name) |
| description | str | yes | Human-readable description |
| version | str | yes | Semver version |
| match_keywords | list[str] | yes | Keywords for auto-matching against job payloads |
| replaces_patterns | list[str] | yes | Patterns in job names/payloads this template replaces |
| parameters | list[object] | yes | Configurable parameters (can be empty list) |
| output_format | str | yes | Output format: "telegram_markdown", "plain", "json" |
| requires | list[str] | no | Python packages needed beyond stdlib |

### Parameter Object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | str | yes | Parameter name (used in script.py as variable) |
| type | str | yes | One of: str, int, float, bool, list |
| required | bool | yes | Whether the parameter must be provided |
| default | any | no | Default value if not provided |
| description | str | yes | What this parameter controls |
| env_var | str | no | Environment variable that can override this |

## script.py Convention

Each script.py is a standalone Python script that:
1. Uses only stdlib + packages listed in manifest.requires
2. Has a `CONFIG` dict at the top with parameter defaults
3. Collects data and formats output
4. Calls a `send_output(message)` function that handles the output channel
5. Has try/except error handling around all major operations
6. Exits with code 0 on success, 1 on failure

### Template Variables
The converter fills these in the CONFIG dict:
- All parameters from manifest.json
- `TELEGRAM_TOKEN` - bot token (from env)
- `TELEGRAM_CHAT_ID` - target chat (from config)

## Converter Mapping

The converter matches CronJob fields to template parameters:
1. **Auto-match**: Compare job payload keywords against template `match_keywords`
2. **Score**: Count keyword overlaps, pick highest-scoring template
3. **Fill params**: Use values from yburn.yaml, env vars, or prompt user
4. **Generate**: Render script.py with filled CONFIG, save as standalone file
