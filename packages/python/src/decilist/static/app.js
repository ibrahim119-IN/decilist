const $ = (id) => document.getElementById(id);
const presets = {
  ready: { complete: true, valid: true, transient: false, retries: 0 },
  retry: { complete: false, valid: true, transient: true, retries: 1 },
  review: { complete: false, valid: false, transient: false, retries: 2 },
};
const instructions = {
  choose: $("instructions").value,
  predict: "Is the workflow ready to proceed?",
  score: "How ready is the workflow?",
};
const storageKey = "decilist.local-tests.v1";
let health,
  lastRun,
  busy = false,
  revision = 0,
  cases = [],
  testResults = new Map();
function notice(message) {
  $("notice").textContent = message;
  $("notice").hidden = false;
}
function parseLines(id) {
  return $(id)
    .value.split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
}
function requestFromForm() {
  const kind = $("kind").value,
    state = $("state").value.trim(),
    questionText = $("instructions").value.trim();
  if (!state || !questionText)
    throw Error("Add a question and example input first.");
  const question = { kind, state, instructions: questionText };
  if (kind === "choose") {
    const entries = parseLines("options").map((line) => {
      const at = line.indexOf("|");
      if (at < 1) throw Error("Write each answer as label | meaning.");
      return [line.slice(0, at).trim(), line.slice(at + 1).trim()];
    });
    if (
      entries.length < 2 ||
      entries.length > 64 ||
      entries.some(
        ([k, v]) => !k || k.length > 256 || !v || v.length > 65536,
      ) ||
      new Set(entries.map(([k]) => k)).size !== entries.length
    )
      throw Error("Add 2–64 distinct answers, each with a label and meaning.");
    question.options = Object.fromEntries(entries);
  } else if (kind === "score") {
    question.levels = parseLines("levels");
    if (
      question.levels.length < 2 ||
      question.levels.length > 10 ||
      question.levels.some((x) => x.length > 256) ||
      new Set(question.levels).size !== question.levels.length
    )
      throw Error("Add 2–10 distinct score levels, ordered lowest to highest.");
  }
  const timeout = Number($("timeout").value);
  if (!Number.isInteger(timeout) || timeout < 1 || timeout > 120000)
    throw Error("Use a time limit between 1 and 120000 milliseconds.");
  return {
    question,
    policy: {
      local_only: true,
      min_confidence: Number($("threshold").value),
      timeout_ms: timeout,
    },
  };
}
function pythonExample(request) {
  const q = request.question,
    arg =
      q.kind === "choose"
        ? `,\n            options=${JSON.stringify(q.options)}`
        : q.kind === "score"
          ? `,\n            levels=${JSON.stringify(q.levels)}`
          : "";
  return `import asyncio\nfrom decilist import Client, Policy\n\nasync def main():\n    async with Client(${JSON.stringify(location.origin)}) as client:\n        result = await client.${q.kind}(\n            state=${JSON.stringify(q.state)},\n            instructions=${JSON.stringify(q.instructions)}${arg},\n            policy=Policy(local_only=True, min_confidence=${request.policy.min_confidence}, timeout_ms=${request.policy.timeout_ms}),\n        )\n        print(result.value if result.status == "accepted" else "Needs review")\n\nasyncio.run(main())`;
}
function typescriptOptions(options) {
  const entries = Object.entries(options).map(([label, meaning]) => {
    const key = JSON.stringify(label);
    // A plain __proto__ literal changes the prototype instead of adding an answer.
    const property = label === "__proto__" ? `[${key}]` : key;
    return `${property}:${JSON.stringify(meaning)}`;
  });
  return `{${entries.join(",")}}`;
}
function typescriptExample(request) {
  const q = request.question;
  const args = [q.state, q.instructions];
  if (q.kind === "choose") args.push(q.options);
  if (q.kind === "score") args.push(q.levels);
  const parameters = args
    .map((value, index) => {
      const literal =
        q.kind === "choose" && index === 2
          ? typescriptOptions(value)
          : JSON.stringify(value);
      return `  ${literal}`;
    })
    .join(",\n");
  return `import { Decilist } from "@decilist/sdk";\n\nconst decilist = await Decilist.connect({\n  baseUrl: ${JSON.stringify(location.origin)},\n  policy: ${JSON.stringify(request.policy)},\n});\nconst result = await decilist.${q.kind}(\n${parameters},\n);\n\nif (result.status === "abstained") {\n  console.log("Needs review");\n} else {\n  console.log(result.value);\n}`;
}
function sdkExample(request) {
  return $("snippet-language").value === "typescript"
    ? typescriptExample(request)
    : pythonExample(request);
}
function updateSnippet() {
  try {
    $("sdk").textContent = sdkExample(requestFromForm());
    $("copy-sdk").disabled = false;
  } catch {
    $("sdk").textContent =
      "Complete the question and input to create an SDK example.";
    $("copy-sdk").disabled = true;
  }
}
$("snippet-language").addEventListener("change", () => {
  updateSnippet();
  $("copy-sdk").textContent = "Copy code";
});
function changed() {
  revision++;
  lastRun = null;
  $("result").hidden = true;
  $("error").hidden = true;
  $("empty").hidden = busy;
  $("input-error").hidden = true;
  updateSnippet();
}
function updateKind() {
  const kind = $("kind").value;
  $("options-field").hidden = kind !== "choose";
  $("levels-field").hidden = kind !== "score";
  if (
    !health ||
    health.provider === "demo" ||
    health.providers?.some((p) => p.model === "rules-v1")
  )
    $("instructions").value = instructions[kind];
  changed();
}
$("decision-form").addEventListener("input", changed);
$("kind").addEventListener("change", updateKind);
document.querySelectorAll("[data-preset]").forEach((button) =>
  button.addEventListener("click", () => {
    $("state").value = JSON.stringify(presets[button.dataset.preset], null, 2);
    changed();
  }),
);
$("threshold").addEventListener("input", () => {
  $("threshold-value").textContent =
    Math.round(Number($("threshold").value) * 100) + "%";
});
async function decide(request) {
  const totalTimeout = Math.min(
    250000,
    request.policy.timeout_ms * Math.max(health?.providers?.length || 2, 1) +
      10000,
  );
  let response;
  try {
    response = await fetch("/v1/decisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: AbortSignal.timeout(totalTimeout),
    });
  } catch (error) {
    throw Error(
      error.name === "TimeoutError"
        ? "The runtime took too long. Your input is kept here; try again or inspect the time limit."
        : "The local runtime couldn't be reached. Start it, then try again.",
    );
  }
  if (!response.ok)
    throw Error(
      response.status === 422
        ? "The runtime rejected this input. Check the question, allowed answers, and time limit."
        : `The runtime couldn't complete the request (${response.status}). Your input is kept here.`,
    );
  return response.json();
}
function setExpected(result, request) {
  const choices =
    request.question.kind === "choose"
      ? Object.keys(request.question.options)
      : request.question.kind === "predict"
        ? ["true", "false"]
        : ["score"];
  $("expected").replaceChildren(
    ...choices.concat("abstained").map((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent =
        value === "abstained"
          ? "Needs review (abstained)"
          : value === "score"
            ? "A numeric score (within 0.05)"
            : value;
      return option;
    }),
  );
  $("expected").value =
    result.status === "abstained"
      ? "abstained"
      : request.question.kind === "score"
        ? "score"
        : String(result.value);
  $("expected-score").max = String((request.question.levels?.length || 2) - 1);
  $("expected-score").value =
    typeof result.value === "number" ? result.value.toFixed(2) : "0";
  updateExpected();
}
function updateExpected() {
  const score = $("expected").value === "score";
  $("expected-score").hidden = !score;
  $("expected-score-label").hidden = !score;
}
$("expected").addEventListener("change", updateExpected);
function revealOutcome(id) {
  const heading = $(id);
  heading.focus({ preventScroll: true });
  heading.scrollIntoView({ block: "nearest", behavior: "instant" });
}
function renderResult(result, request) {
  $("empty").hidden = true;
  $("result").hidden = false;
  $("error").hidden = true;
  const q = request.question,
    accepted = result.status === "accepted";
  $("status").textContent = accepted ? "ANSWER ACCEPTED" : "NEEDS REVIEW";
  $("status").classList.toggle("warning", !accepted);
  $("result-mode").textContent =
    health?.provider === "demo" ? "Rules baseline" : "Local runtime";
  $("latency").textContent = result.latency_ms.toFixed(1) + " ms";
  $("value").textContent = !accepted
    ? "No answer"
    : q.kind === "predict"
      ? result.value
        ? "True"
        : "False"
      : q.kind === "score"
        ? `${result.value.toFixed(2)} / ${q.levels.length - 1}`
        : String(result.value);
  $("explanation").textContent = !accepted
    ? "No provider met the policy. Let your application ask for detail, retry, or request review."
    : q.kind === "choose"
      ? q.options[result.value]
      : q.kind === "predict"
        ? "The predicate returned this answer for your input."
        : `Ordered from “${q.levels[0]}” to “${q.levels.at(-1)}”. This is an expected position, not a rounded category.`;
  $("probabilities").replaceChildren();
  for (const [key, p] of Object.entries(result.probabilities)) {
    const row = document.createElement("div");
    row.className = "prob";
    const label = document.createElement("div");
    label.className = "prob-label";
    const name = document.createElement("span");
    name.textContent = q.kind === "score" ? q.levels[Number(key)] : key;
    const value = document.createElement("span");
    value.textContent = (p * 100).toFixed(1) + "%";
    label.append(name, value);
    const meter = document.createElement("meter");
    meter.min = 0;
    meter.max = 1;
    meter.value = p;
    meter.setAttribute(
      "aria-label",
      `${name.textContent}: ${value.textContent}`,
    );
    row.append(label, meter);
    $("probabilities").append(row);
  }
  $("provider").textContent = result.provider
    ? `${result.provider} · ${result.model} · confidence ${(result.confidence * 100).toFixed(1)}%`
    : "No accepted provider. See attempts below.";
  $("trace").replaceChildren();
  for (const attempt of result.attempts) {
    const li = document.createElement("li"),
      name = document.createElement("span"),
      outcome = document.createElement("span");
    name.textContent = attempt.provider;
    outcome.textContent =
      attempt.outcome.replaceAll("_", " ") +
      ` · ${attempt.latency_ms.toFixed(1)} ms`;
    li.append(name, outcome);
    $("trace").append(li);
  }
  $("json").textContent = JSON.stringify(result, null, 2);
  $("test-name").value = "";
  setExpected(result, request);
  lastRun = { result, request };
  revealOutcome("result-heading");
}
$("decision-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  let request;
  try {
    request = requestFromForm();
  } catch (error) {
    $("input-error").textContent = error.message;
    $("input-error").hidden = false;
    return;
  }
  const startRevision = revision;
  busy = true;
  $("run").disabled = true;
  $("run-tests").disabled = true;
  $("run").textContent = "Running…";
  $("result").hidden = true;
  $("empty").hidden = true;
  $("error").hidden = true;
  $("loading").hidden = false;
  document.querySelector(".result-panel").setAttribute("aria-busy", "true");
  try {
    const result = await decide(request);
    if (startRevision === revision) renderResult(result, request);
    else
      notice(
        "Your input changed while the decision ran. Run again to check the new input.",
      );
  } catch (error) {
    if (startRevision === revision) {
      $("error-message").textContent = error.message;
      $("error").hidden = false;
      revealOutcome("error-heading");
    }
  } finally {
    busy = false;
    $("run").disabled = false;
    $("run").textContent = "Run decision ↗";
    $("run-tests").disabled = !cases.length;
    $("loading").hidden = true;
    $("empty").hidden = !($("result").hidden && $("error").hidden);
    document.querySelector(".result-panel").setAttribute("aria-busy", "false");
  }
});
$("copy-sdk").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(sdkExample(requestFromForm()));
    $("copy-sdk").textContent = "Copied";
  } catch {
    const details = $("sdk").closest("details");
    details.open = true;
    const selection = window.getSelection(),
      range = document.createRange();
    range.selectNodeContents($("sdk"));
    selection.removeAllRanges();
    selection.addRange(range);
    notice("The SDK example is selected. Use your usual copy shortcut.");
  }
  setTimeout(() => {
    $("copy-sdk").textContent = "Copy code";
  }, 2000);
});
function persist() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(cases));
    return true;
  } catch {
    notice(
      "This browser couldn't save the test. Check available local storage.",
    );
    return false;
  }
}
function renderCases() {
  $("test-count").textContent = cases.length;
  $("tests-empty").hidden = !!cases.length;
  $("run-tests").disabled = !cases.length || busy;
  $("test-list").replaceChildren();
  for (const item of cases) {
    const li = document.createElement("li"),
      info = document.createElement("div"),
      name = document.createElement("strong"),
      summary = document.createElement("span"),
      remove = document.createElement("button");
    name.textContent = item.name;
    summary.textContent = `${item.request.question.kind} · expected ${item.expected.status === "abstained" ? "abstained" : item.expected.value} · ${testResults.get(item.id) || "Not run"}`;
    info.append(name, summary);
    remove.type = "button";
    remove.className = "quiet";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove test ${item.name}`);
    remove.disabled = busy;
    remove.addEventListener("click", () => {
      const before = cases;
      cases = cases.filter((x) => x.id !== item.id);
      if (!persist()) cases = before;
      else {
        testResults.delete(item.id);
        notice(`Removed “${item.name}” from this device.`);
      }
      renderCases();
    });
    li.append(info, remove);
    $("test-list").append(li);
  }
}
$("save-case").addEventListener("click", () => {
  if (!lastRun) {
    notice("Run the current input before saving a test.");
    return;
  }
  const kind = lastRun.request.question.kind,
    chosen = $("expected").value;
  let value = chosen;
  if (chosen === "score") {
    value = Number($("expected-score").value);
    if (
      !Number.isFinite(value) ||
      value < 0 ||
      value > lastRun.request.question.levels.length - 1 ||
      !$("expected-score").value
    ) {
      notice("Enter an expected score within this rubric.");
      return;
    }
  }
  if (kind === "predict" && chosen !== "abstained") value = chosen === "true";
  const item = {
    id: crypto.randomUUID(),
    name: $("test-name").value.trim() || `Example ${cases.length + 1}`,
    request: structuredClone(lastRun.request),
    expected:
      chosen === "abstained"
        ? { status: "abstained", value: null }
        : { status: "accepted", value },
  };
  if (cases.length >= 100) {
    notice(
      "This workspace keeps up to 100 tests. Remove one before adding another.",
    );
    return;
  }
  cases.push(item);
  if (!persist()) {
    cases.pop();
    return;
  }
  renderCases();
  $("tests-details").open = true;
  notice(`Saved “${item.name}” on this device.`);
});
$("run-tests").addEventListener("click", async () => {
  busy = true;
  $("run").disabled = true;
  $("run-tests").disabled = true;
  $("run-tests").textContent = "Running tests…";
  renderCases();
  let passed = 0;
  for (const item of cases) {
    testResults.set(item.id, "Running…");
    renderCases();
    try {
      const result = await decide(item.request);
      const ok =
        result.status === item.expected.status &&
        (result.status === "abstained" ||
          (typeof item.expected.value === "number"
            ? Math.abs(result.value - item.expected.value) <= 0.05
            : result.value === item.expected.value));
      if (ok) passed++;
      testResults.set(
        item.id,
        ok
          ? "Passed"
          : `Failed (got ${result.status === "abstained" ? "abstained" : result.value})`,
      );
    } catch (error) {
      testResults.set(item.id, `Error: ${error.message}`);
    }
    renderCases();
  }
  busy = false;
  $("run").disabled = false;
  $("run-tests").textContent = "Run tests";
  renderCases();
  notice(`${passed} of ${cases.length} local tests passed.`);
});
try {
  const saved = JSON.parse(localStorage.getItem(storageKey) || "[]");
  if (Array.isArray(saved))
    cases = saved
      .filter(
        (item) =>
          item &&
          typeof item.name === "string" &&
          typeof item.id === "string" &&
          item.request?.question &&
          item.request?.policy &&
          item.expected &&
          ["accepted", "abstained"].includes(item.expected.status),
      )
      .slice(0, 100);
} catch {
  notice("Saved tests couldn't be read. New tests can still be created.");
}
renderCases();
updateSnippet();
async function loadHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw Error();
    health = await response.json();
    const baseline =
      health.provider === "demo" ||
      health.providers?.some((p) => p.model === "rules-v1");
    $("runtime").textContent = baseline
      ? "RULES WALKTHROUGH · LOCAL"
      : "LOCAL MODEL · READY";
    $("mode-note").textContent = baseline
      ? "This is a wiring demo with fixed workflow rules and a uniform baseline. It does not demonstrate model intelligence. Use a local model to try your own question."
      : "Decisions use the local model configured for this runtime. Inspect its identity and every attempt in the result details.";
    $("provider-details").textContent = JSON.stringify(health, null, 2);
    for (const id of ["instructions", "options", "levels"])
      $(id).readOnly = baseline;
    $("input-hint").textContent = baseline
      ? "workflow state as JSON"
      : "text or structured evidence";
    $("run").disabled = false;
  } catch {
    $("runtime").textContent = "RUNTIME UNAVAILABLE";
    $("mode-note").textContent =
      "Start the local runtime, then refresh this page. Your input stays here.";
    $("run").disabled = true;
  }
}
loadHealth();
