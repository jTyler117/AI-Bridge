"""A stand-in for the `claude` and `codex` CLIs, used ONLY by selftest.py.

It records how it was called, then does what the real CLIs do in subscription mode: start the MCP tool server named in
the config it was given and call a tool on it, then print a reply in the CLI's output format.
Scenario comes from $FAKE_CLI_SCENARIO: chat | write | sleep | fail_auth | tools. Records to $FAKE_CLI_CAPTURE.
"""
import asyncio
import json
import os
import re
import sys
import time

flavor, argv = sys.argv[1], sys.argv[2:]
scenario = os.environ.get("FAKE_CLI_SCENARIO", "chat")
cap_path = os.environ.get("FAKE_CLI_CAPTURE", "")


def capture(d):
    if cap_path:
        with open(cap_path, "w", encoding="utf-8") as f:
            json.dump(d, f)


def opt(name):
    return argv[argv.index(name) + 1] if name in argv else None


# status subcommands
if argv[:2] in (["auth", "status"], ["login", "status"]):
    ok = os.environ.get("FAKE_CLI_LOGGED_IN", "1") == "1"
    if flavor == "claude":
        print(json.dumps({"loggedIn": ok}))
    else:
        print("Logged in using ChatGPT" if ok else "Not logged in")
    sys.exit(0 if ok else 1)

stdin = sys.stdin.read()
rec = {"argv": argv, "stdin": stdin, "env_keys": sorted(k for k in os.environ if "API_KEY" in k or "AUTH_TOKEN" in k),
       "cwd": os.getcwd(), "mcp_tool_timeout": os.environ.get("MCP_TOOL_TIMEOUT")}

# ---- find the tool server this CLI was told to use
if flavor == "claude":
    cfg = json.load(open(opt("--mcp-config"), encoding="utf-8"))["mcpServers"]["aibridge"]
    command, args, env = cfg["command"], cfg["args"], cfg["env"]
    rec["system"] = open(opt("--append-system-prompt-file"), encoding="utf-8").read()
else:
    ov = {}
    for i, a in enumerate(argv):
        if a == "-c":
            k, v = argv[i + 1].split("=", 1)
            ov[k] = v
    command = json.loads(ov["mcp_servers.aibridge.command"])
    args = json.loads(ov["mcp_servers.aibridge.args"])
    env = dict(re.findall(r'(\w+)=("(?:[^"\\]|\\.)*")', ov["mcp_servers.aibridge.env"]))
    env = {k: json.loads(v) for k, v in env.items()}
    rec["tool_timeout_sec"] = ov.get("mcp_servers.aibridge.tool_timeout_sec")
    rec["approval_mode"] = ov.get("mcp_servers.aibridge.default_tools_approval_mode")
rec["server"] = {"command": command, "args": args, "env": env}

if scenario == "fail_auth":
    capture(rec)
    print("Error: not logged in. Please login first.", file=sys.stderr)
    sys.exit(1)
if scenario == "sleep":
    rec["started"] = True
    capture(rec)
    time.sleep(120)
    sys.exit(0)


async def use_tools():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    out = {}
    async with stdio_client(StdioServerParameters(command=command, args=args, env=env)) as (r, w):
        async with ClientSession(r, w) as sess:
            await sess.initialize()
            out["tools"] = sorted(t.name for t in (await sess.list_tools()).tools)
            if scenario == "bridgetest":
                res = await sess.call_tool("read_file", {"path": "bridge_connection_test.txt"})
                out["bridge_reply"] = "BRIDGE OK " + re.search(r"[0-9a-f]{8}", " ".join(getattr(c, "text", "") for c in res.content)).group(0)
            if scenario == "write":
                res = await sess.call_tool("write_file", {"path": "from_cli.txt", "content": "written through the guarded tool"})
                out["write_result"] = " ".join(getattr(c, "text", "") for c in res.content)
            if scenario == "memory":  # read shared memory, then propose a lesson, all through the real MCP server
                txt = lambda res: " ".join(getattr(c, "text", "") for c in res.content)
                out["ctx"] = txt(await sess.call_tool("get_project_context", {}))
                out["read"] = txt(await sess.call_tool("read_project_memory", {"file": "PROJECT.md"}))
                out["propose"] = txt(await sess.call_tool("propose_memory_update", {
                    "file": "LESSONS.md", "title": f"{flavor} found a quirk",
                    "content": ({"claude": "Smoothing custom normals fixes the shading seams along the torso shells.",
                                 "codex": "Four pixel texture padding prevents colour bleeding at atlas island edges."}[flavor]
                                + f" Unique marker {flavor}-7731."),
                    "status": "verified", "evidence": f"{flavor} ran the export and counted meshes"}))
            if scenario == "image":  # look at a shared image, then post one, through the real MCP server
                iid = os.environ["FAKE_IMG_ID"]
                res = await sess.call_tool("view_image", {"image_id": iid})
                out["view_kinds"] = [getattr(c, "type", None) for c in res.content]
                out["view_mime"] = [getattr(c, "mimeType", None) or getattr(c, "mime_type", None) for c in res.content if getattr(c, "type", None) == "image"]
                out["list"] = " ".join(getattr(c, "text", "") for c in (await sess.call_tool("list_images", {})).content)
                out["share"] = " ".join(getattr(c, "text", "") for c in (await sess.call_tool("share_image", {"source": iid, "note": f"{flavor} re-shares it"})).content)
            if scenario == "memory_write":  # a direct write: lands only if the live mode is Automatic
                txt = lambda res: " ".join(getattr(c, "text", "") for c in res.content)
                out["propose"] = txt(await sess.call_tool("write_project_memory", {
                    "file": "PROGRESS.md", "title": "Direct write from CLI", "content": "Shader compile finished without warnings on the second attempt."}))
    return out


reply = "hello from the fake CLI. [DONE]"
if scenario in ("write", "tools", "bridgetest", "memory", "memory_write", "image"):
    got = asyncio.run(use_tools())
    rec.update(got)
    reply = got.get("write_result") or got.get("bridge_reply") or ("memory: " + got["propose"] if "propose" in got else ("image: " + got["share"] if "share" in got else "tools listed"))
if scenario == "blocked":  # what real `codex exec` does to MCP calls when it can't ask anyone
    capture(rec)
    print("mcp: aibridge/get_state failed: user cancelled MCP tool call")
    open(opt("-o"), "w", encoding="utf-8").write("I can't complete this: the Blender MCP call was blocked because it requires approval "
                                                "while this session's approval policy is `never`.")
    sys.exit(0)
capture(rec)

if flavor == "claude":
    print(json.dumps({"type": "result", "is_error": False, "result": reply,
                      "usage": {"input_tokens": 10, "output_tokens": 5}}))
else:
    open(opt("-o"), "w", encoding="utf-8").write(reply)
    print(json.dumps({"event": "done"}))
