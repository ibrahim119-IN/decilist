import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

// Exercise the shipped generator without booting the unrelated browser workspace.
const source = await readFile(
  new URL("../packages/python/src/decilist/static/app.js", import.meta.url),
  "utf8",
);
const start = source.indexOf("function typescriptOptions(");
const end = source.indexOf("function sdkExample(");
assert.ok(
  start >= 0 && end > start,
  "the shipped snippet generator is present",
);
const generate = vm.runInNewContext(
  `${source.slice(start, end)}; typescriptExample;`,
  { location: { origin: "http://127.0.0.1:8776" } },
);

test("generated TypeScript preserves prototype-like answer labels as own keys", async () => {
  const options = JSON.parse(
    '{"__proto__":"Prototype label","constructor":"Constructor label","other":"Other label"}',
  );
  const request = {
    question: {
      kind: "choose",
      state: 'Quoted "input" with `ticks`, ${text}, العربية\nand a new line',
      instructions: "Choose one supplied answer.",
      options,
    },
    policy: { local_only: true, min_confidence: 0.7, timeout_ms: 30000 },
  };
  const snippet = generate(request);
  assert.ok(
    snippet.includes('["__proto__"]:'),
    "special key uses computed syntax",
  );
  assert.ok(
    snippet.includes('"constructor":'),
    "ordinary keys keep normal syntax",
  );

  let seen;
  const messages = [];
  const decilist = {
    async connect(configuration) {
      assert.deepEqual(configuration, {
        baseUrl: "http://127.0.0.1:8776",
        policy: request.policy,
      });
      return {
        async choose(state, instructions, actual) {
          seen = actual;
          assert.equal(state, request.question.state);
          assert.equal(instructions, request.question.instructions);
          return { status: "accepted", value: "__proto__" };
        },
      };
    },
  };
  const body = snippet.replace(
    /^import \{ Decilist \} from "@decilist\/sdk";\s*/,
    "",
  );
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction("Decilist", "console", body)(decilist, {
    log: (value) => messages.push(value),
  });
  assert.deepEqual(Object.keys(seen), ["__proto__", "constructor", "other"]);
  assert.deepEqual(seen, options);
  assert.equal(Object.hasOwn(seen, "__proto__"), true);
  assert.equal(Object.getPrototypeOf(seen), Object.prototype);
  assert.deepEqual(messages, ["__proto__"]);
});
