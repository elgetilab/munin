#!/bin/bash
################################################################################
# DEEP RESEARCH JOB - SLURM SCRIPT
################################################################################
# Runs a deep research job using the MiroThinker 30B thinking model.
# Features:
#   - 64k context window
#   - Tool access: web search (SearXNG), paper search, DOI lookup
#   - Agentic loop for multi-step research
#
# Required environment variables:
#   JOB_DIR     - Directory containing input.txt and for output
#   REQUEST_ID  - Unique request identifier
################################################################################

#SBATCH --job-name=deepresearch
#SBATCH --partition=llm-batch
#SBATCH --gres=gpu:batch:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --output=/opt/munin/deepresearch/jobs/%x-%j.log
#SBATCH --error=/opt/munin/deepresearch/jobs/%x-%j.log

set -e

################################################################################
# Error Handler - Update status to failed on crash
################################################################################
update_status_failed() {
    local exit_code=$?
    if [ $exit_code -ne 0 ] && [ -n "$JOB_DIR" ] && [ -d "$JOB_DIR" ]; then
        echo "[ERROR] Job failed with exit code $exit_code, updating status..."
        python3 << PYEOF
import json
import os
from datetime import datetime

job_dir = "$JOB_DIR"
status_file = os.path.join(job_dir, "status.json")
try:
    # Read existing status if available
    status = {}
    if os.path.exists(status_file):
        with open(status_file) as f:
            status = json.load(f)

    # Update to failed
    status["status"] = "failed"
    status["completed_at"] = datetime.now().isoformat()
    status["error"] = "Job crashed with exit code $exit_code"
    status.pop("slurm_state", None)  # Remove SLURM state

    with open(status_file, "w") as f:
        json.dump(status, f, indent=2)
    print(f"Status updated to failed in {status_file}")
except Exception as e:
    print(f"Warning: Failed to update status file: {e}")
PYEOF
    fi
}

# Set trap to update status on any error exit
trap update_status_failed EXIT

################################################################################
# Configuration
################################################################################
# MiroThinker model - downloaded by deploy.sh deepresearch
export MODEL_ID="/opt/munin/data/models/mirothinker-v1.5-30b"

# 64k context window (single job at a time)
export MAX_MODEL_LEN=65536
export GPU_MEMORY_UTILIZATION=0.88
export MAX_NUM_SEQS=1  # Only one request at a time - reduces memory overhead
export MAX_TOKENS=16384
export TEMPERATURE=1.0  # MiroThinker recommends 1.0

# Tool endpoints (internal cluster services)
export SEARXNG_URL="http://localhost:8888"
export RETRIEVAL_URL="http://localhost:8080"

# Agentic settings (exported for Python script)
export MAX_TOOL_ITERATIONS=40      # With one-tool-per-message, need more iterations
export MAX_TOOLS_PER_ITERATION=5   # Limit tools per iteration to prevent context overflow
export MAX_TOOL_RESULT_CHARS=3000  # Truncate each tool result to prevent context bloat
export MAX_CONTEXT_CHARS=180000    # ~45k tokens - leave room for response

################################################################################
# Setup
################################################################################
echo "=============================================="
echo "DEEP RESEARCH JOB (with Tools)"
echo "=============================================="
echo "Job ID:       $SLURM_JOB_ID"
echo "Request ID:   $REQUEST_ID"
echo "Job Dir:      $JOB_DIR"
echo "Node:         $SLURMD_NODENAME"
echo "Start Time:   $(date)"
echo "Model:        $MODEL_ID"
echo "Context:      $MAX_MODEL_LEN tokens"
echo "Tools:        SearXNG, Paper Search, DOI Lookup"
echo "=============================================="

# Validate required variables
if [ -z "$JOB_DIR" ] || [ -z "$REQUEST_ID" ]; then
    echo "[ERROR] JOB_DIR and REQUEST_ID must be set"
    exit 1
fi

# Verify job directory exists
if [ ! -d "$JOB_DIR" ]; then
    echo "[ERROR] Job directory not found: $JOB_DIR"
    exit 1
fi

# Read input
INPUT_FILE="$JOB_DIR/input.txt"
if [ ! -f "$INPUT_FILE" ]; then
    echo "[ERROR] Input file not found: $INPUT_FILE"
    exit 1
fi

QUESTION=$(cat "$INPUT_FILE")
echo ""
echo "Research Question:"
echo "----------------------------------------"
echo "$QUESTION"
echo "----------------------------------------"
echo ""

# Update status function
update_status() {
    local status=$1
    shift
    cat > "$JOB_DIR/status.json" << EOF
{
    "request_id": "$REQUEST_ID",
    "slurm_job_id": "$SLURM_JOB_ID",
    "status": "$status",
    "question": $(python3 -c "import json; print(json.dumps('''$QUESTION'''))"),
    "submitted_at": "$(cat "$JOB_DIR/request.json" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('submitted_at',''))" 2>/dev/null || echo '')",
    "started_at": "$(date -Iseconds)"$@
}
EOF
}

# Update status to running
update_status "running"

# Activate vLLM environment
source /opt/munin/services/vllm/venv/bin/activate

# Set GPU environment
export CUDA_DEVICE_ORDER=PCI_BUS_ID

################################################################################
# Run Agentic Research with Tools
################################################################################
echo "Starting agentic research with tool access..."

OUTPUT_FILE="$JOB_DIR/output.md"

# Disable set -e for the Python script: vLLM engine cleanup can return
# non-zero even when the research completed successfully.
set +e
python3 << 'PYTHON_SCRIPT'
import json
import os
import re
import requests
import time
from datetime import datetime
from vllm import LLM, SamplingParams

################################################################################
# Configuration
################################################################################
model_id = os.environ.get("MODEL_ID", "/opt/munin/data/models/mirothinker-v1.5-30b")
max_model_len = int(os.environ.get("MAX_MODEL_LEN", 65536))
gpu_memory = float(os.environ.get("GPU_MEMORY_UTILIZATION", 0.88))
max_num_seqs = int(os.environ.get("MAX_NUM_SEQS", 1))
temperature = float(os.environ.get("TEMPERATURE", 1.0))  # MiroThinker recommends 1.0
max_tokens = int(os.environ.get("MAX_TOKENS", 16384))
job_dir = os.environ["JOB_DIR"]
output_file = os.path.join(job_dir, "output.md")

searxng_url = os.environ.get("SEARXNG_URL", "http://localhost:8888")
retrieval_url = os.environ.get("RETRIEVAL_URL", "http://localhost:8080")
max_iterations = int(os.environ.get("MAX_TOOL_ITERATIONS", 20))  # Increased for one-tool-per-message
max_tool_result_chars = int(os.environ.get("MAX_TOOL_RESULT_CHARS", 3000))

# Read input
with open(os.path.join(job_dir, "input.txt")) as f:
    question = f.read().strip()

today_date = datetime.now().strftime("%Y-%m-%d")

################################################################################
# Tool Definitions in MiroThinker JSONSchema Format
################################################################################
TOOLS_SCHEMA = """
## Server name: munin_search

### Tool name: web_search
Description: Search the web using SearXNG meta-search engine. Use this to find recent information, news, or general web content about any topic.
Input JSON schema: {"type": "object", "properties": {"query": {"type": "string", "description": "The search query"}, "top_k": {"type": "integer", "description": "Number of results (default: 10)", "default": 10}}, "required": ["query"]}

### Tool name: paper_search
Description: Search the Munin LOCAL paper database using semantic search (SPECTER). Only searches papers that have been ingested into the local knowledge base. Use semantic_scholar_search for broader academic search.
Input JSON schema: {"type": "object", "properties": {"query": {"type": "string", "description": "The search query for finding papers"}, "top_k": {"type": "integer", "description": "Number of results (default: 10)", "default": 10}}, "required": ["query"]}

### Tool name: semantic_scholar_search
Description: Search Semantic Scholar for academic papers across 200M+ publications. Returns papers with titles, authors, DOIs, citation counts, abstracts, and AI-generated TLDRs. THIS IS THE PRIMARY TOOL for finding academic papers. Supports year filtering.
Input JSON schema: {"type": "object", "properties": {"query": {"type": "string", "description": "The search query"}, "top_k": {"type": "integer", "description": "Number of results (default: 10, max: 100)", "default": 10}, "year": {"type": "string", "description": "Optional year filter (e.g., '2020-2024', '2024-', '2024')", "default": ""}}, "required": ["query"]}

### Tool name: paper_lookup
Description: Look up detailed information about a specific paper by its DOI. Returns full metadata including title, authors, journal, year, abstract, and citation counts.
Input JSON schema: {"type": "object", "properties": {"doi": {"type": "string", "description": "The DOI of the paper to look up"}}, "required": ["doi"]}

### Tool name: get_citations
Description: Find papers that CITE a given paper. Use this to discover follow-up research and how a paper influenced the field.
Input JSON schema: {"type": "object", "properties": {"doi": {"type": "string", "description": "The DOI of the paper"}, "limit": {"type": "integer", "description": "Max results (default: 20)", "default": 20}}, "required": ["doi"]}

### Tool name: get_references
Description: Find papers CITED BY a given paper (its bibliography). Use this to find foundational work and understand what research a paper builds upon.
Input JSON schema: {"type": "object", "properties": {"doi": {"type": "string", "description": "The DOI of the paper"}, "limit": {"type": "integer", "description": "Max results (default: 50)", "default": 50}}, "required": ["doi"]}

### Tool name: get_author_papers
Description: Find all papers by a specific author. Supports partial name matching.
Input JSON schema: {"type": "object", "properties": {"author_name": {"type": "string", "description": "Author name (partial match supported)"}, "limit": {"type": "integer", "description": "Max results (default: 50)", "default": 50}}, "required": ["author_name"]}

### Tool name: get_paper_pdf
Description: Check if a PDF is available for download and get the download URL.
Input JSON schema: {"type": "object", "properties": {"doi": {"type": "string", "description": "The DOI of the paper"}}, "required": ["doi"]}

### Tool name: web_fetch
Description: Fetch and read the full content of a webpage. Use after web_search to read promising URLs. Can optionally summarize long content.
Input JSON schema: {"type": "object", "properties": {"url": {"type": "string", "description": "The URL to fetch"}, "summarize": {"type": "boolean", "description": "If true, summarize content using LLM", "default": false}, "summary_instruction": {"type": "string", "description": "Instructions for summarization", "default": "Summarize the main points"}}, "required": ["url"]}

### Tool name: llm_summarize
Description: Use an LLM to summarize or extract specific information from text. Useful for processing long documents.
Input JSON schema: {"type": "object", "properties": {"text": {"type": "string", "description": "The text to process"}, "instruction": {"type": "string", "description": "Instructions (e.g., 'Summarize in 3 bullet points')"}}, "required": ["text", "instruction"]}
"""

################################################################################
# MiroThinker System Prompt
################################################################################
SYSTEM_PROMPT = f"""You are MiroThinker, an advanced AI research assistant developed by MiroMind, adapted for deep research tasks on the Munin cluster.

In this environment you have access to a set of tools you can use to answer the user's research question.

You only have access to the tools provided below. You can only use one tool per message, and will receive the result of that tool in the user's next response. You use tools step-by-step to accomplish a given task, with each tool-use informed by the result of the previous tool-use. Today is: {today_date}

# Tool-Use Formatting Instructions

Tool use is formatted using XML-style tags. The tool use must be at the end of your response, at the top-level and not nested inside any other content.

To use a tool, write the following format:
<use_mcp_tool>
<server_name>munin_search</server_name>
<tool_name>tool_name_here</tool_name>
<arguments>
{{
  "param1": "value1",
  "param2": "value2"
}}
</arguments>
</use_mcp_tool>

Important rules:
- You can ONLY use one tool per message
- Arguments must be valid JSON with properly escaped strings
- Tool use must appear at the END of your response

# Tool Definitions
{TOOLS_SCHEMA}

# Research Strategy - FOLLOW THIS EXACTLY

You MUST follow this structured research process. Do NOT get stuck repeating the same tool.

## PHASE 1: Discovery (5-8 tool calls)
Goal: Find relevant sources from BOTH academic papers AND web.

REQUIRED actions:
1. Use `semantic_scholar_search` with 2-3 different query formulations to find academic papers (this searches 200M+ papers and is the PRIMARY academic search tool)
2. Use `web_search` with 2-3 different queries (for recent news, tutorials, reviews)
3. Optionally use `paper_search` to check the local knowledge base
4. Note the most relevant DOIs and URLs for Phase 2

RULE: Do NOT repeat any single search tool more than 4 times total. Move on after finding good results.

## PHASE 2: Deep Investigation (8-12 tool calls)
Goal: Get detailed information from the best sources found in Phase 1.

REQUIRED actions:
1. Use `semantic_scholar_search` with refined queries or year filters to find specific papers
2. Use `get_citations` on 1-2 highly-cited papers to find follow-up research
3. Use `get_references` on 1-2 key papers to find foundational work
4. Use `web_fetch` on 2-4 promising URLs to read full content

RULE: You MUST use web_fetch in this phase to read full content. Do NOT skip it.

## PHASE 3: Write Report (NO MORE TOOLS)
Goal: Synthesize findings into a comprehensive report.

After 15-25 tool calls total, STOP using tools and write your report.

CRITICAL: When you have gathered enough information, write the report directly WITHOUT any tool call. Do not say "I will now write the report" and then use a tool - just write it.

# Report Structure (REQUIRED)

Your final report MUST include these sections with Markdown headers:

## Executive Summary
Brief overview of key findings (3-5 sentences)

## Background and Context
What is this topic? Why is it important?

## Key Findings
Main discoveries from your research. CITE specific papers with DOIs and web sources with URLs.

## Current State of Research
What are researchers currently working on? Recent developments?

## Challenges and Open Questions
What problems remain unsolved? What are the limitations?

## Future Directions
Where is the field heading? What opportunities exist?

## Conclusion
Synthesis of the research and final thoughts.

## References
List ALL papers and sources you found, formatted as:
- [Author et al., Year] Title. DOI: xxx (if available)
- [Website] URL

# CRITICAL RULES

1. **DIVERSITY**: Use DIFFERENT tools. Do not repeat any single tool more than 4 times.
2. **BALANCE**: Use BOTH `semantic_scholar_search` AND `web_search`. Academic + web sources.
3. **DEPTH**: Use `web_fetch` to read full content from promising URLs, not just search snippets.
4. **STOP SEARCHING**: After ~20 tool calls, STOP and WRITE. More searching ≠ better report.
5. **CITE EVERYTHING**: Every claim should reference a specific paper (with DOI) or URL you found.
6. **PREFER semantic_scholar_search**: For academic papers, use `semantic_scholar_search` as your primary tool. It covers 200M+ papers. Use `paper_search` only if you specifically want to check the local knowledge base.

Think through the problem using <think> tags, but keep thinking concise. Focus on ACTION."""

################################################################################
# Tool Implementations - Using MCP REST Endpoint
################################################################################
def call_mcp_tool(tool_name: str, arguments: dict, timeout: int = 120) -> str:
    """
    Call a tool via the MCP REST endpoint.

    This uses the unified /mcp/call endpoint in the retrieval service,
    avoiding code duplication between Deep Research and Open WebUI.
    """
    try:
        response = requests.post(
            f"{retrieval_url}/mcp/call",
            json={"name": tool_name, "arguments": arguments},
            timeout=timeout
        )
        response.raise_for_status()
        result = response.json()

        # Check for error in result
        if isinstance(result, dict) and "error" in result:
            return f"Tool error: {result['error']}"

        # Format result as readable string for the model
        return format_tool_result(tool_name, result)
    except requests.Timeout:
        return f"Tool '{tool_name}' timed out after {timeout}s"
    except requests.RequestException as e:
        return f"Tool '{tool_name}' request failed: {str(e)}"
    except Exception as e:
        return f"Tool '{tool_name}' error: {str(e)}"


def format_tool_result(tool_name: str, result: dict) -> str:
    """Format tool result as readable text for the model."""
    if tool_name == "web_search":
        results = result.get("results", [])
        if not results:
            return "No web results found."
        lines = [f"Web search results ({len(results)} found):"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n{i}. **{r.get('title', 'No title')}**")
            if r.get('url'):
                lines.append(f"   URL: {r['url']}")
            if r.get('snippet'):
                lines.append(f"   {r['snippet'][:300]}")
        return "\n".join(lines)

    elif tool_name == "paper_search":
        results = result.get("results", [])
        if not results:
            return "No papers found."
        lines = [f"Paper search results ({len(results)} found):"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n{i}. **{r.get('title', 'Untitled')}**")
            if r.get('authors'):
                authors = r['authors'][:3]
                author_str = ", ".join(authors) + (" et al." if len(r['authors']) > 3 else "")
                lines.append(f"   Authors: {author_str}")
            if r.get('year'):
                lines.append(f"   Year: {r['year']}")
            if r.get('doi'):
                lines.append(f"   DOI: {r['doi']}")
            if r.get('score'):
                lines.append(f"   Relevance: {r['score']:.2f}")
        return "\n".join(lines)

    elif tool_name == "semantic_scholar_search":
        results = result.get("results", [])
        if not results:
            return "No papers found on Semantic Scholar."
        total = result.get("total", len(results))
        lines = [f"Semantic Scholar results ({len(results)} shown, {total} total):"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n{i}. **{r.get('title', 'Untitled')}**")
            if r.get('authors'):
                authors = r['authors'][:3]
                author_str = ", ".join(authors) + (" et al." if len(r['authors']) > 3 else "")
                lines.append(f"   Authors: {author_str}")
            if r.get('year'):
                lines.append(f"   Year: {r['year']}")
            if r.get('doi'):
                lines.append(f"   DOI: {r['doi']}")
            if r.get('citation_count') is not None:
                lines.append(f"   Citations: {r['citation_count']}")
            if r.get('journal'):
                lines.append(f"   Journal: {r['journal']}")
            if r.get('tldr'):
                lines.append(f"   TLDR: {r['tldr']}")
            elif r.get('abstract'):
                lines.append(f"   Abstract: {r['abstract'][:200]}...")
        return "\n".join(lines)

    elif tool_name == "paper_lookup":
        if "error" in result:
            return f"Paper not found: {result['error']}"
        lines = [f"**{result.get('title', 'Untitled')}**\n"]
        if result.get('authors'):
            lines.append(f"**Authors:** {', '.join(result['authors'])}")
        if result.get('year'):
            lines.append(f"**Year:** {result['year']}")
        if result.get('journal'):
            lines.append(f"**Journal:** {result['journal']}")
        if result.get('doi'):
            lines.append(f"**DOI:** {result['doi']}")
        if result.get('citation_count'):
            lines.append(f"**Citations:** {result['citation_count']}")
        if result.get('abstract'):
            lines.append(f"\n**Abstract:**\n{result['abstract']}")
        return "\n".join(lines)

    elif tool_name == "get_citations":
        if "error" in result:
            return f"Citation lookup failed: {result['error']}"
        papers = result.get("citing_papers", [])
        if not papers:
            return f"No citing papers found for DOI: {result.get('doi', '?')}"
        lines = [f"Papers citing {result.get('doi', '?')} ({result.get('citation_count', len(papers))} found):"]
        for i, p in enumerate(papers, 1):
            lines.append(f"\n{i}. **{p.get('title', 'Untitled')}** ({p.get('year', '?')})")
            if p.get('doi'):
                lines.append(f"   DOI: {p['doi']}")
        return "\n".join(lines)

    elif tool_name == "get_references":
        if "error" in result:
            return f"Reference lookup failed: {result['error']}"
        refs = result.get("references", [])
        if not refs:
            return f"No references found for DOI: {result.get('doi', '?')}"
        lines = [f"References from {result.get('doi', '?')} ({result.get('reference_count', len(refs))} found):"]
        for i, p in enumerate(refs, 1):
            lines.append(f"\n{i}. **{p.get('title', 'Untitled')}** ({p.get('year', '?')})")
            if p.get('doi'):
                lines.append(f"   DOI: {p['doi']}")
        return "\n".join(lines)

    elif tool_name == "get_author_papers":
        if "error" in result:
            return f"Author lookup failed: {result['error']}"
        papers = result.get("papers", [])
        if not papers:
            return f"No papers found for author: {result.get('author_query', '?')}"
        lines = [f"Papers by '{result.get('author_query', '?')}' ({result.get('paper_count', len(papers))} found):"]
        for i, p in enumerate(papers, 1):
            lines.append(f"\n{i}. **{p.get('title', 'Untitled')}** ({p.get('year', '?')})")
            if p.get('doi'):
                lines.append(f"   DOI: {p['doi']}")
        return "\n".join(lines)

    elif tool_name == "get_paper_pdf":
        if "error" in result:
            return f"PDF check failed: {result['error']}"
        if result.get("pdf_available"):
            return f"PDF available for {result.get('doi', '?')}: {result.get('download_url', '')}"
        msg = f"PDF not available locally for {result.get('doi', '?')}."
        suggestions = result.get("suggestions", {})
        if suggestions.get("sci_hub"):
            msg += f"\nTry Sci-Hub: {suggestions['sci_hub']}"
        return msg

    elif tool_name == "web_fetch":
        if "error" in result:
            return f"Failed to fetch URL: {result['error']}"
        lines = [f"**Content from:** {result.get('url', 'Unknown URL')}"]
        lines.append(f"**Length:** {result.get('content_length', 0)} characters\n")
        # If summarized, show summary first
        if result.get('summary'):
            lines.append("**Summary:**")
            lines.append(result['summary'])
            lines.append("\n---\n**Full content preview:**")
        # Show content (truncated for context management)
        content = result.get('content', '')
        if len(content) > 3000:
            lines.append(content[:3000] + "\n\n[... content truncated ...]")
        else:
            lines.append(content)
        return "\n".join(lines)

    elif tool_name == "llm_summarize":
        if "error" in result:
            return f"Summarization failed: {result['error']}"
        return result.get('summary', 'No summary generated')

    else:
        # Generic JSON formatting for other tools
        return json.dumps(result, indent=2)


def parse_mcp_tool_call(response_text: str) -> dict | None:
    """Parse MiroThinker's <use_mcp_tool> format."""
    match = re.search(r'<use_mcp_tool>(.*?)</use_mcp_tool>', response_text, re.DOTALL)
    if not match:
        return None

    content = match.group(1)

    server_match = re.search(r'<server_name>(.*?)</server_name>', content, re.DOTALL)
    tool_match = re.search(r'<tool_name>(.*?)</tool_name>', content, re.DOTALL)
    args_match = re.search(r'<arguments>(.*?)</arguments>', content, re.DOTALL)

    if not (server_match and tool_match and args_match):
        return None

    try:
        args = json.loads(args_match.group(1).strip())
    except json.JSONDecodeError as e:
        print(f"[WARNING] Failed to parse tool arguments: {e}")
        return None

    return {
        "server_name": server_match.group(1).strip(),
        "tool_name": tool_match.group(1).strip(),
        "arguments": args
    }


def execute_mcp_tool(tool_call: dict) -> str:
    """Execute a parsed MCP tool call via the REST endpoint."""
    tool_name = tool_call["tool_name"]
    args = tool_call["arguments"]
    return call_mcp_tool(tool_name, args)


################################################################################
# Main Agentic Loop - One Tool Per Message
################################################################################
print("Loading model...")
llm = LLM(
    model=model_id,
    max_model_len=max_model_len,
    gpu_memory_utilization=gpu_memory,
    max_num_seqs=max_num_seqs,
    trust_remote_code=True
)

sampling_params = SamplingParams(
    temperature=temperature,
    top_p=0.95,
    repetition_penalty=1.05,
    max_tokens=max_tokens,
    stop=["<|im_end|>", "<|endoftext|>"]
)

# Tool usage log
tool_results_log = []

def build_conversation_prompt(messages: list[dict]) -> str:
    """Build prompt from conversation history using ChatML format."""
    prompt = ""
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        prompt += f"<|im_start|>{role}\n{content}\n<|im_end|>\n"
    prompt += "<|im_start|>assistant\n"
    return prompt

def generate(messages: list[dict]) -> str:
    """Generate a response from conversation history."""
    prompt = build_conversation_prompt(messages)
    outputs = llm.generate([prompt], sampling_params)
    return outputs[0].outputs[0].text

def estimate_tokens(text: str) -> int:
    """Rough token estimate (4 chars per token)."""
    return len(text) // 4

def truncate_conversation(messages: list[dict], max_chars: int = 150000) -> list[dict]:
    """Truncate older messages if conversation gets too long, keeping system and last few turns."""
    total_chars = sum(len(m["content"]) for m in messages)
    if total_chars <= max_chars:
        return messages

    # Keep system message and last N exchanges
    system_msg = messages[0] if messages[0]["role"] == "system" else None
    other_msgs = messages[1:] if system_msg else messages

    # Keep last 6 messages (3 exchanges)
    truncated = other_msgs[-6:]
    if system_msg:
        truncated = [system_msg] + truncated

    print(f"[WARNING] Truncated conversation from {len(messages)} to {len(truncated)} messages")
    return truncated

print(f"\nStarting MiroThinker research with up to {max_iterations} tool iterations...\n")
print(f"Using MCP tool format: <use_mcp_tool>...</use_mcp_tool>")
print(f"One tool per message, results fed back as user messages\n")

# Build initial conversation
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": f"Please research the following question thoroughly:\n\n{question}"}
]

# Main agentic loop - one tool per message
final_response = None
consecutive_no_tool = 0

for iteration in range(max_iterations):
    print(f"\n[Iteration {iteration + 1}/{max_iterations}]")

    # Truncate if needed
    messages = truncate_conversation(messages)

    # Generate response
    response = generate(messages)
    print(f"  Response length: {len(response)} chars")

    # Check for tool call
    tool_call = parse_mcp_tool_call(response)

    if tool_call:
        consecutive_no_tool = 0
        tool_name = tool_call["tool_name"]
        tool_args = tool_call["arguments"]

        print(f"  Tool: {tool_name}")
        print(f"  Args: {json.dumps(tool_args)[:80]}...")

        # Execute the tool
        tool_result = execute_mcp_tool(tool_call)

        # Truncate result if needed
        if len(tool_result) > max_tool_result_chars:
            tool_result = tool_result[:max_tool_result_chars] + "\n... (truncated)"

        # Log the tool call
        tool_results_log.append({
            "tool_name": tool_name,
            "arguments": tool_args,
            "result": tool_result
        })

        print(f"  Result: {len(tool_result)} chars")

        # Add assistant's response (with tool call) and user's response (tool result)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Tool result:\n\n{tool_result}"})

    else:
        consecutive_no_tool += 1
        print(f"  No tool call detected (consecutive: {consecutive_no_tool})")

        # Check if this looks like a final report
        # Require MULTIPLE sections to avoid false positives from thinking content
        report_sections = [
            "executive summary", "background", "key findings",
            "conclusion", "references", "current state", "challenges"
        ]
        sections_found = sum(1 for section in report_sections if section in response.lower())
        is_report = sections_found >= 3  # Require at least 3 sections

        # Accept as final report if:
        # 1. Has multiple report sections and is long enough, OR
        # 2. We've had too many consecutive no-tool responses (model is done researching)
        min_tool_calls = 8
        max_consecutive_no_tool = 3  # Force report after this many non-tool responses

        has_enough_tools = len(tool_results_log) >= min_tool_calls
        gave_up_on_tools = consecutive_no_tool >= max_consecutive_no_tool

        if is_report and len(response) > 3000 and (has_enough_tools or gave_up_on_tools):
            print(f"  Final report detected ({len(response)} chars, {sections_found} sections, {len(tool_results_log)} tools)")
            final_response = response
            break

        # Force break if model refuses to use tools or write a report
        if consecutive_no_tool >= max_consecutive_no_tool + 2:
            print(f"  Model stuck after {consecutive_no_tool} non-tool responses, forcing final report generation...")
            break

        # If no tool and not a report, prompt to continue or finish
        messages.append({"role": "assistant", "content": response})

        if consecutive_no_tool >= max_consecutive_no_tool:
            # Model clearly doesn't want to use more tools - ask for report
            print(f"  {consecutive_no_tool} consecutive non-tool responses, prompting final report...")
            messages.append({
                "role": "user",
                "content": "You have gathered enough information. Please write your comprehensive research report now. Do NOT use any more tools. Write the full report with all sections: Executive Summary, Background, Key Findings, Current State, Challenges, Future Directions, Conclusion, and References."
            })
        elif len(tool_results_log) < min_tool_calls and consecutive_no_tool < 2:
            # Haven't done enough research yet - push to use more tools (but only gently)
            print(f"  Only {len(tool_results_log)} tool calls so far, encouraging more research...")
            messages.append({
                "role": "user",
                "content": f"You have used {len(tool_results_log)} tools so far. Please continue researching - use paper_search, web_search, paper_lookup, web_fetch, or get_citations to gather more information, or write your final report if you have enough."
            })
        else:
            # Encourage report writing
            print(f"  Prompting model to write final report...")
            messages.append({
                "role": "user",
                "content": "You have gathered enough information. Please write your comprehensive research report now. Do NOT use any more tools. Write the full report with all sections."
            })

# If we didn't get a final response in the loop, generate one
if final_response is None:
    print(f"\n[Final] Generating final report...")
    messages.append({
        "role": "user",
        "content": "Please write your final comprehensive research report now. Include all sections: Executive Summary, Background, Key Findings, Current State, Challenges, Future Directions, Conclusion, and References."
    })
    final_response = generate(messages)

# Clean up response - remove any remaining tool calls or think tags
clean_response = re.sub(r'<use_mcp_tool>.*?</use_mcp_tool>', '', final_response, flags=re.DOTALL)
# Strip complete <think>...</think> pairs
clean_response = re.sub(r'<think>.*?</think>', '', clean_response, flags=re.DOTALL)
# Strip incomplete <think>... without closing tag (truncated by max_tokens)
clean_response = re.sub(r'<think>.*$', '', clean_response, flags=re.DOTALL)
# Strip ALL orphaned </think> tags (can appear multiple times)
clean_response = re.sub(r'</think>', '', clean_response)
# Strip ALL orphaned <think> tags
clean_response = re.sub(r'<think>', '', clean_response)

# Find the actual report start (first markdown header) and discard preamble thinking
# This handles cases where the model outputs thinking text before starting the report
report_match = re.search(r'(^|\n)(##?\s+(?:Executive Summary|Background|Key Findings|Introduction|Overview))', clean_response, re.IGNORECASE)
if report_match:
    clean_response = clean_response[report_match.start():].strip()

clean_response = clean_response.strip()

################################################################################
# Write Output
################################################################################
with open(output_file, "w") as f:
    f.write(f"# Deep Research Report\n\n")
    f.write(f"**Research Question:** {question}\n\n")
    f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
    f.write(f"**Tools Used:** {len(tool_results_log)} tool calls\n\n")
    f.write(f"---\n\n")
    f.write(clean_response)

    # Append tool usage log
    if tool_results_log:
        f.write(f"\n\n---\n\n## Appendix: Tool Usage Log\n\n")
        for i, log_entry in enumerate(tool_results_log, 1):
            f.write(f"### Tool Call {i}: {log_entry['tool_name']}\n")
            f.write(f"**Arguments:**\n```json\n{json.dumps(log_entry['arguments'], indent=2)}\n```\n\n")
            # Truncate long results for appendix
            result = log_entry['result']
            if len(result) > 2000:
                result = result[:2000] + "\n... (truncated in appendix)"
            f.write(f"**Result:**\n{result}\n\n")

print(f"\nOutput written to: {output_file}")
print(f"Report length: {len(clean_response)} characters")
print(f"Total tool calls: {len(tool_results_log)}")

# Explicitly shut down vLLM engine to avoid orphaned process / race condition
# Without this, the engine core process dies during garbage collection and
# returns a non-zero exit code that propagates to the bash script.
import gc
del llm
gc.collect()
print("[OK] vLLM engine shut down cleanly")
PYTHON_SCRIPT
PYTHON_EXIT=$?
set -e

# Even if the Python script returns non-zero due to vLLM engine cleanup,
# the job succeeded if output.md exists. Check that before bailing out.
if [ $PYTHON_EXIT -ne 0 ] && [ -f "$OUTPUT_FILE" ]; then
    echo "[WARNING] Python exited with code $PYTHON_EXIT (likely vLLM engine cleanup), but output exists - continuing"
elif [ $PYTHON_EXIT -ne 0 ]; then
    echo "[ERROR] Python script failed with exit code $PYTHON_EXIT"
    exit $PYTHON_EXIT
fi

# Check if output was generated
if [ ! -f "$OUTPUT_FILE" ]; then
    echo "[ERROR] Output file not generated"
    update_status "failed" ', "error": "Output file not generated"'
    exit 1
fi

OUTPUT_SIZE=$(wc -c < "$OUTPUT_FILE")
echo ""
echo "Output generated successfully ($OUTPUT_SIZE bytes)"

################################################################################
# Generate PDF
################################################################################
echo ""
echo "Generating PDF..."

PDF_FILE="$JOB_DIR/output.pdf"

# Check if pandoc is available
if command -v pandoc &> /dev/null; then
    # Try with pdflatex first
    if command -v pdflatex &> /dev/null; then
        pandoc "$OUTPUT_FILE" \
            -o "$PDF_FILE" \
            --pdf-engine=pdflatex \
            -V geometry:margin=1in \
            -V fontsize=11pt \
            --toc \
            --toc-depth=2 \
            -f markdown+smart \
            --standalone \
            2>/dev/null && echo "[OK] PDF generated with pdflatex" || {
                echo "[WARNING] pdflatex failed, trying wkhtmltopdf..."
            }
    fi

    # Fallback: try with wkhtmltopdf
    if [ ! -f "$PDF_FILE" ] && command -v wkhtmltopdf &> /dev/null; then
        pandoc "$OUTPUT_FILE" -o "$JOB_DIR/output.html" -s --css=- 2>/dev/null
        if [ -f "$JOB_DIR/output.html" ]; then
            wkhtmltopdf --quiet "$JOB_DIR/output.html" "$PDF_FILE" 2>/dev/null && {
                echo "[OK] PDF generated with wkhtmltopdf"
                rm -f "$JOB_DIR/output.html"
            } || {
                echo "[WARNING] wkhtmltopdf failed"
                rm -f "$JOB_DIR/output.html"
            }
        fi
    fi

    if [ -f "$PDF_FILE" ]; then
        PDF_SIZE=$(wc -c < "$PDF_FILE")
        echo "PDF size: $PDF_SIZE bytes"
    else
        echo "[WARNING] PDF generation not available - markdown output only"
    fi
else
    echo "[WARNING] pandoc not found, skipping PDF generation"
fi

################################################################################
# Completion
################################################################################
COMPLETED_AT=$(date -Iseconds)

# Read original submission time
SUBMITTED_AT=$(cat "$JOB_DIR/request.json" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('submitted_at',''))" 2>/dev/null || echo '')

cat > "$JOB_DIR/status.json" << EOF
{
    "request_id": "$REQUEST_ID",
    "slurm_job_id": "$SLURM_JOB_ID",
    "status": "completed",
    "question": $(python3 -c "import json; print(json.dumps('''$QUESTION'''))"),
    "submitted_at": "$SUBMITTED_AT",
    "started_at": "$(date -Iseconds)",
    "completed_at": "$COMPLETED_AT"
}
EOF

# Clear error trap on successful completion
trap - EXIT

echo ""
echo "=============================================="
echo "JOB COMPLETED"
echo "=============================================="
echo "End Time:     $(date)"
echo "Output MD:    $OUTPUT_FILE"
if [ -f "$PDF_FILE" ]; then
    echo "Output PDF:   $PDF_FILE"
fi
echo "=============================================="
