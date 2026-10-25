#!/usr/bin/env node
/** Explicit foreground bridge to the installed Python runtime. No shell or install side effects. */
import { spawn } from "node:child_process";
import { launcherArgs } from "./cli-args.js";

let parsed: ReturnType<typeof launcherArgs>;
try { parsed = launcherArgs(process.argv.slice(2), process.env.DECILIST_PYTHON || "python"); }
catch (error) {
  console.error(`Decilist: ${(error as Error).message}`);
  process.exit(2);
}
const child = spawn(parsed.python, ["-m", "decilist", ...parsed.args], {stdio: "inherit", windowsHide: true, shell: false});
child.once("error", () => {
  console.error("Decilist: Cannot launch Python. Install decilist[server] in a local Python environment and pass --python PATH or set DECILIST_PYTHON.");
  process.exitCode = 1;
});
// Windows sends console Ctrl+C to the inherited Python console too. Terminating a venv
// redirector PID could orphan its interpreter, so do not replace that console signal.
process.on("SIGINT", () => { if (process.platform !== "win32") child.kill("SIGINT"); });
if (process.platform !== "win32") process.on("SIGTERM", () => { child.kill("SIGTERM"); });
child.once("exit", (code, signal) => { process.exitCode = code ?? (signal === "SIGINT" ? 130 : 1); });
