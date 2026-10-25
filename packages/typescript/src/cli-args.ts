/** Parse only launcher options. Arguments after -- belong unchanged to the application. */
export function launcherArgs(input: string[], defaultPython = "python"): {python: string; args: string[]} {
  const args = [...input];
  const separator = args.indexOf("--");
  const pythonIndex = args.findIndex((value, index) => value === "--python" && (separator === -1 || index < separator));
  let python = defaultPython;
  if (pythonIndex !== -1) {
    const selected = args[pythonIndex + 1];
    if (!selected || selected.startsWith("--")) throw new Error("--python requires the path to a Python environment with decilist[server] installed.");
    python = selected;
    args.splice(pythonIndex, 2);
  }
  return {python, args};
}
