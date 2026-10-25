import { Decilist } from "../packages/typescript/dist/index.js";
const sdk = new Decilist();
const state = JSON.stringify({complete: false, valid: true, transient: true, retries: 1});
const result = await sdk.choose(state, "Choose the next workflow action.", {proceed: "Complete and valid", retry: "Transient failure with retries remaining", review: "Needs a person"});
console.log(JSON.stringify(result, null, 2));
if (result.status !== "accepted" || result.value !== "retry") process.exitCode = 1;
