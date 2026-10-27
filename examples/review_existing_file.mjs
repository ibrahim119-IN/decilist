// Run from the built workspace against `decilist review serve`.
import { open } from "node:fs/promises";
import { extname } from "node:path";
import { Decilist } from "@decilist/sdk";

const [path, task] = process.argv.slice(2);
const languages = {".js":"JavaScript", ".mjs":"JavaScript", ".cjs":"JavaScript", ".jsx":"JSX", ".ts":"TypeScript", ".tsx":"TSX"};
if (!path || !task || !languages[extname(path).toLowerCase()]) {
  console.error('Usage: node examples/review_existing_file.mjs existing.ts "Intended behavior"');
  process.exit(2);
}
const file = await open(path, "r");
let code;
try {
  const buffer = Buffer.alloc(262145);
  const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
  if (bytesRead > 262144) throw new Error("Review a smaller source module");
  code = new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, bytesRead));
} finally {
  await file.close();
}
const sdk = await Decilist.connect({ baseUrl: process.env.DECILIST_REVIEW_URL ?? "http://127.0.0.1:8766" });
const result = await sdk.review(code, { task, language: languages[extname(path).toLowerCase()] });
console.log(JSON.stringify(result, null, 2));
process.exitCode = result.status === "unavailable" ? 2 : 0;
