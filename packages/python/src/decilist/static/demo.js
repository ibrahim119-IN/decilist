const $ = (id) => document.getElementById(id);
let busy = false,
  revision = 0,
  originalRequest = "";
function changed() {
  revision++;
  $("demo-response").hidden = true;
  $("demo-error").hidden = true;
  $("clarification").value = "";
}
$("request").addEventListener("input", changed);
document.querySelectorAll("[data-request]").forEach((button) =>
  button.addEventListener("click", () => {
    $("request").value = button.dataset.request;
    changed();
    $("request").focus();
  }),
);
async function prepare(clarification = "") {
  const request = $("request").value.trim();
  if (!request) {
    $("demo-error").textContent = "Write a request first.";
    $("demo-error").hidden = false;
    $("request").focus();
    return;
  }
  if (request.length > 4000 || clarification.length > 1000) {
    $("demo-error").textContent = "Please shorten your request and try again.";
    $("demo-error").hidden = false;
    return;
  }
  if (busy) return;
  busy = true;
  const startRevision = revision;
  originalRequest = request;
  $("prepare").disabled = true;
  $("continue").disabled = true;
  $("demo-pending").hidden = false;
  $("demo-response").hidden = true;
  $("demo-error").hidden = true;
  try {
    const response = await fetch("/v1/demo/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request, clarification }),
      signal: AbortSignal.timeout(75000),
    });
    if (!response.ok)
      throw Error(
        response.status === 422
          ? "Please shorten your request and try again."
          : "We couldn't check this request right now. Please try again.",
      );
    const result = await response.json();
    if (revision !== startRevision) return;
    $("demo-state").textContent =
      {
        proceed: "DRAFT READY",
        clarify: "ONE DETAIL",
        review: "NEEDS REVIEW",
        unavailable: "TRY AGAIN",
      }[result.outcome] || "NEEDS REVIEW";
    $("demo-state").classList.toggle(
      "warning",
      ["review", "unavailable"].includes(result.outcome),
    );
    $("demo-message").textContent = result.message;
    $("demo-response").hidden = false;
    $("clarification-form").hidden = result.outcome !== "clarify";
    $("draft").hidden = result.outcome !== "proceed";
    $("review-help").hidden = !["review", "unavailable"].includes(
      result.outcome,
    );
    if (result.outcome === "proceed") {
      $("draft-request").textContent = result.draft.request;
      $("draft-clarification").textContent = result.draft.clarification;
      $("draft-clarification").hidden = !result.draft.clarification;
    }
    if (result.outcome === "clarify") $("clarification").focus();
    else $("demo-message").focus();
  } catch (error) {
    if (revision === startRevision) {
      $("demo-error").textContent =
        error.name === "TimeoutError"
          ? "This is taking longer than expected. Your request is kept here. Please try again."
          : error.message === "Failed to fetch"
            ? "The local service isn't available. Your request is kept here; try again when it is ready."
            : error.message;
      $("demo-error").hidden = false;
    }
  } finally {
    busy = false;
    $("prepare").disabled = false;
    $("continue").disabled = false;
    $("demo-pending").hidden = true;
  }
}
$("request-form").addEventListener("submit", (event) => {
  event.preventDefault();
  $("clarification").value = "";
  prepare();
});
$("clarification-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if ($("request").value.trim() !== originalRequest) {
    changed();
    prepare();
    return;
  }
  const answer = $("clarification").value.trim();
  if (answer) prepare(answer);
});
async function loadHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw Error();
    const data = await response.json(),
      baseline =
        data.provider === "demo" ||
        data.providers?.some((p) => p.model === "rules-v1");
    $("demo-mode").textContent = baseline
      ? "Rules walkthrough · local"
      : "Local model example";
    $("demo-mode-explanation").textContent = baseline
      ? "This walkthrough uses authored rules and recognizes a small set of English and Arabic phrases. It demonstrates the integration flow, not model intelligence. Try the example requests above."
      : "This example uses the local model connected to the runtime. A model may be uncertain or wrong; the app handles review and only prepares a preview.";
  } catch {
    $("demo-mode").textContent = "Service unavailable";
    $("demo-mode-explanation").textContent =
      "The local service is unavailable. Requests remain in this page so you can retry.";
  }
}
loadHealth();
