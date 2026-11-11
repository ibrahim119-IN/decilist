import { z } from "zod";
import { reviewRequestSchema, validateReviewResult, type ReviewOptions, type ReviewRequest, type ReviewResult } from "./code-review.js";
export { reviewRequestSchema, reviewPolicySchema, reviewResultSchema, validateReviewResult } from "./code-review.js";
export type { ReviewPolicy, ReviewOptions, ReviewRequest, ReviewResult } from "./code-review.js";

const text = z.string().trim().min(1).max(65536);
const label = z.string().min(1).max(256).refine(value => value === value.trim());
function isRecord(input: unknown): input is Record<PropertyKey, unknown> {
  if (typeof input !== "object" || input === null || Array.isArray(input)) return false;
  // Match record input semantics, including null-prototype dictionaries and own constructor labels.
  const constructor: unknown = (input as {constructor?: unknown}).constructor;
  if (typeof constructor !== "function") return true;
  const prototype: unknown = constructor.prototype;
  return typeof prototype === "object" && prototype !== null && !Array.isArray(prototype) && Object.hasOwn(prototype, "isPrototypeOf");
}
function stringRecord<Value extends z.ZodType>(key: z.ZodType<string>, value: Value) {
  // Zod's record output skips __proto__. Validate own entries before constructing safe data properties.
  return z.custom<Record<PropertyKey, unknown>>(isRecord, "Expected a record")
    .transform(input => Reflect.ownKeys(input).filter(key => Object.prototype.propertyIsEnumerable.call(input, key)).map(key => [key, input[key]]))
    .pipe(z.array(z.tuple([key, value])))
    .transform((entries): Record<string, z.output<Value>> => Object.fromEntries(entries));
}
const base = { state: text, instructions: text };
export const questionSchema = z.discriminatedUnion("kind", [
  z.strictObject({ ...base, kind: z.literal("choose"), options: stringRecord(label, text).refine(x => Object.keys(x).length >= 2 && Object.keys(x).length <= 64) }),
  z.strictObject({ ...base, kind: z.literal("predict") }),
  z.strictObject({ ...base, kind: z.literal("score"), levels: z.array(label).min(2).max(10).refine(x => new Set(x).size === x.length) }),
]);
export type Question = z.infer<typeof questionSchema>;
export type Policy = { local_only?: boolean; min_confidence?: number; timeout_ms?: number };
export const policySchema = z.strictObject({
  local_only: z.boolean().default(true),
  min_confidence: z.number().min(0).max(1).default(0.7),
  timeout_ms: z.number().int().min(1).max(120000).default(5000),
});
export const decisionSchema = z.strictObject({
  schema_version: z.literal("1"), request_id: z.string().min(1),
  kind: z.enum(["choose", "predict", "score"]), status: z.enum(["accepted", "abstained"]),
  value: z.union([z.string(), z.boolean(), z.number(), z.null()]),
  probabilities: stringRecord(z.string(), z.number().min(0).max(1)),
  confidence: z.number().min(0).max(1).nullable(), provider: z.string().nullable(), model: z.string().nullable(),
  latency_ms: z.number().min(0), cost_usd: z.number().min(0).nullable(),
  attempts: z.array(z.strictObject({provider: z.string(), outcome: z.enum(["accepted", "low_confidence", "invalid_output", "timeout", "error", "blocked"]), latency_ms: z.number().min(0)})),
});
type Envelope = Omit<z.infer<typeof decisionSchema>, "status" | "value">;
export type Decision<T> = Envelope & ({status: "accepted"; value: T} | {status: "abstained"; value: null});
export type TriedOption<T extends string> = {label: T; ok: boolean; latencyMs: number; error: string | null};
export type VerifiedDecision<T extends string, R = unknown, F = unknown> = {
  decision: Decision<T>; tried: TriedOption<T>[];
  /** Present when the choice was checked in cost order. */
  costed?: CostedDecision<T>;
} & (
  | {status: "verified"; value: T; result: R}
  | {status: "fallback"; value: null; result: F}
  | {status: "unresolved"; value: null; result: null}
);

/** A calibration written by `decilist fit-calibration`. Temperature above 1 makes the model less sure of itself. */
/** The fields are checked when used, so an imported calibration JSON file can be passed as it is. */
export type Calibration = {temperature: number; kind: string; labels: readonly string[]; model?: string | null; min_confidence?: number | null};

/** Refuse a malformed calibration, or one fitted for another kind of question or other answers. */
function checkCalibration(calibration: Calibration, kind: string, labels: string[]): void {
  if (!isRecord(calibration)) throw new TypeError("calibration must be an object");
  if (typeof calibration.temperature !== "number" || !Number.isFinite(calibration.temperature) || calibration.temperature <= 0) throw new RangeError("temperature must be a finite number above 0");
  if (calibration.kind !== "choose" && calibration.kind !== "predict") throw new RangeError("calibration applies to choose and predict, not score");
  if (calibration.kind !== kind) throw new RangeError(`this calibration was fitted for a ${calibration.kind} question, not ${kind}`);
  if (!Array.isArray(calibration.labels) || calibration.labels.length !== labels.length || [...calibration.labels].sort().join("\u0000") !== [...labels].sort().join("\u0000"))
    throw new RangeError("this calibration was fitted for different answers than the question has");
  const floor = calibration.min_confidence;
  if (floor !== undefined && floor !== null && (typeof floor !== "number" || !(floor >= 0 && floor <= 1))) throw new RangeError("min_confidence must be in [0, 1]");
}
/** Corrected probabilities for the same labels. The most probable answer does not change, and zero stays zero. */
export function applyCalibration(probabilities: Record<string, number>, calibration: Calibration, kind: string = calibration.kind): Record<string, number> {
  const labels = Object.keys(probabilities);
  checkCalibration(calibration, kind, labels);
  if (calibration.temperature === 1) return {...probabilities};
  const logs = labels.map(label => probabilities[label]! > 0 ? Math.log(probabilities[label]!) / calibration.temperature : -Infinity);
  const top = Math.max(...logs);
  const weights = logs.map(value => Math.exp(value - top));
  const total = weights.reduce((a, b) => a + b, 0);
  return Object.fromEntries(labels.map((label, index) => [label, weights[index]! / total]));
}
/**
 * Correct an accepted choose or predict decision, then apply a threshold to the corrected confidence.
 * Ask with min_confidence 0 first. A decision from another model than the calibration's is not corrected.
 */
export function calibrateDecision<T>(decision: Decision<T>, calibration: Calibration, minConfidence = 0): Decision<T> {
  if (decision.status !== "accepted") return decision;
  if (decision.kind === "score") throw new RangeError("calibration applies to choose and predict, not score");
  let probabilities = decision.probabilities;
  let confidence = decision.confidence ?? 0;
  checkCalibration(calibration, decision.kind, Object.keys(probabilities));
  if (calibration.model === undefined || calibration.model === null || calibration.model === decision.model) {
    probabilities = applyCalibration(probabilities, calibration, decision.kind);
    confidence = Math.max(...Object.values(probabilities));
  }
  if (confidence < minConfidence) {
    const attempts = decision.attempts.map(attempt => attempt.outcome === "accepted" ? {...attempt, outcome: "low_confidence" as const} : attempt);
    return {...decision, status: "abstained", value: null, probabilities: {}, confidence: null, provider: null, model: null, attempts};
  }
  return {...decision, probabilities, confidence};
}

/** What each answer costs: wrong[answered][actual]. Unlisted wrong answers cost `default` (1); a right answer costs 0. */
export type Costs = {wrong?: Record<string, Record<string, number>>; default?: number; abstain: number | null};
/** The cheapest answer under your costs, or an abstention when asking a person is cheaper. */
export type CostedDecision<T> = {
  /** Expected cost of every answer. Empty when the model itself abstained. */
  expectedCosts: Record<string, number>; abstainCost: number | null;
  /** The model's own decision. Its value is the most probable answer, which can differ from value here. */
  decision: Decision<T>;
} & ({status: "accepted"; value: T; expectedCost: number} | {status: "abstained"; value: null; expectedCost: null});

function checkCost(value: unknown, what: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new RangeError(`${what} must be a finite number of at least 0`);
  return value;
}
/** Reject malformed costs and costs for labels the question does not have: a typo here is silent otherwise. */
function checkCosts(costs: Costs, labels: string[]): void {
  if (!isRecord(costs) || !Object.hasOwn(costs, "abstain")) throw new TypeError("costs must be an object with abstain");
  if (Object.keys(costs).some(key => !["wrong", "default", "abstain"].includes(key))) throw new TypeError("costs has an unknown key");
  if (costs.abstain !== null) checkCost(costs.abstain, "abstain");
  if (costs.default !== undefined) checkCost(costs.default, "default");
  if (costs.wrong === undefined) return;
  if (!isRecord(costs.wrong)) throw new TypeError("wrong must map answered to actual to cost");
  const unknown = new Set<string>();
  for (const [answered, row] of Object.entries(costs.wrong)) {
    if (!isRecord(row)) throw new TypeError("wrong must map answered to actual to cost");
    if (!labels.includes(answered)) unknown.add(answered);
    for (const [actual, cost] of Object.entries(row)) {
      checkCost(cost, "each cost");
      if (!labels.includes(actual)) unknown.add(actual);
    }
  }
  if (unknown.size) throw new RangeError(`costs name labels the question does not have: ${[...unknown].sort().join(", ")}`);
}
function costOf(costs: Costs, answered: string, actual: string): number {
  const row = costs.wrong !== undefined && Object.hasOwn(costs.wrong, answered) ? costs.wrong[answered]! : undefined;
  if (row !== undefined && Object.hasOwn(row, actual)) return row[actual]!;
  return answered === actual ? 0 : costs.default ?? 1;
}
/** Expected cost of each answer under the given probabilities. */
export function expectedCosts(probabilities: Record<string, number>, costs: Costs): Record<string, number> {
  const labels = Object.keys(probabilities);
  checkCosts(costs, labels);
  return Object.fromEntries(labels.map(answered => [answered, labels.reduce((sum, actual) => sum + probabilities[actual]! * costOf(costs, answered, actual), 0)]));
}
/** Answers no costlier than asking a person, cheapest first. Ties go to the more probable answer. */
function rankByCost(probabilities: Record<string, number>, costs: Costs): {ranked: string[]; expected: Record<string, number>} {
  const expected = expectedCosts(probabilities, costs);
  const ranked = Object.keys(expected).sort((a, b) => expected[a]! - expected[b]! || probabilities[b]! - probabilities[a]!)
    .filter(label => costs.abstain === null || expected[label]! <= costs.abstain);
  return {ranked, expected};
}
/** Apply costs to a choose or predict decision. Ask with min_confidence 0 so every probability is present. */
export function applyCosts<T>(decision: Decision<T>, costs: Costs): CostedDecision<T> {
  if (decision.kind === "score") throw new RangeError("costs apply to choose and predict, not score");
  if (decision.status !== "accepted") {
    checkCosts(costs, Object.keys(costs.wrong ?? {}).concat(...Object.values(costs.wrong ?? {}).map(row => isRecord(row) ? Object.keys(row) : [])));
    return {status: "abstained", value: null, expectedCost: null, expectedCosts: {}, abstainCost: costs.abstain, decision};
  }
  const {ranked, expected} = rankByCost(decision.probabilities, costs);
  const best = ranked[0];
  if (best === undefined) return {status: "abstained", value: null, expectedCost: null, expectedCosts: expected, abstainCost: costs.abstain, decision};
  const value = (decision.kind === "predict" ? best === "true" : best) as T;
  return {status: "accepted", value, expectedCost: expected[best]!, expectedCosts: expected, abstainCost: costs.abstain, decision};
}

export function validateDecision<T>(question: Question, input: unknown): Decision<T> {
  const result = decisionSchema.parse(input);
  if (result.kind !== question.kind) throw new Error("Response kind mismatch");
  if (result.status === "abstained") {
    if (result.value !== null || result.confidence !== null || Object.keys(result.probabilities).length) throw new Error("Abstention must have no decision value");
  } else {
    const keys = question.kind === "choose" ? Object.keys(question.options) : question.kind === "predict" ? ["false", "true"] : question.levels.map((_, i) => String(i));
    if (keys.length !== Object.keys(result.probabilities).length || keys.some(k => !Object.hasOwn(result.probabilities, k))) throw new Error("Response labels mismatch");
    const probabilities = keys.map(k => result.probabilities[k]!);
    if (Math.abs(probabilities.reduce((a, b) => a + b, 0) - 1) > 1e-5) throw new Error("Invalid probability distribution");
    const confidence = Math.max(...probabilities);
    const value = question.kind === "choose" ? keys[probabilities.indexOf(confidence)] : question.kind === "predict" ? result.probabilities.true! >= 0.5 : probabilities.reduce((sum, p, i) => sum + p * i, 0);
    if (result.confidence === null || Math.abs(result.confidence - confidence) > 1e-5 || result.provider === null || result.model === null) throw new Error("Invalid response metadata");
    if (typeof value === "number" ? typeof result.value !== "number" || Math.abs(value - result.value) > 1e-5 : value !== result.value) throw new Error("Response value does not match probabilities");
  }
  return result as Decision<T>;
}

/** Transports return the canonical envelope. Policies execute in the Python runtime. */
export interface Transport {
  decide(question: Question, policy: z.infer<typeof policySchema>): Promise<unknown>;
  review?(request: ReviewRequest): Promise<unknown>;
}

export class RuntimeConnectionError extends Error {
  constructor(message: string, options?: ErrorOptions) { super(message, options); this.name = "RuntimeConnectionError"; }
}
export class RuntimeCompatibilityError extends RuntimeConnectionError {
  constructor(message: string, options?: ErrorOptions) { super(message, options); this.name = "RuntimeCompatibilityError"; }
}
export const runtimeHealthSchema = z.object({
  service: z.literal("decilist"), schema_version: z.literal("1"),
  runtime_version: z.string().min(1), ready: z.boolean(), status: z.string(),
  provider: z.string().optional(),
});
export type RuntimeHealth = z.infer<typeof runtimeHealthSchema>;
function defaultUrl(): string {
  // Browser use needs no Node globals; the explicit launcher passes this only to local applications.
  const environment = (globalThis as {process?: {env?: Record<string, string | undefined>}}).process?.env;
  return environment?.DECILIST_URL || "http://127.0.0.1:8765";
}

export class HttpTransport implements Transport {
  private isReady = false;
  constructor(readonly baseUrl = defaultUrl(), readonly timeoutMs = 130000) {
    const url = new URL(baseUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") throw new Error("API URL must be an HTTP(S) origin without credentials, path, query, or fragment");
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error("timeoutMs must be positive");
  }
  /** Wait for identity, readiness, and contract compatibility. Never starts a process. */
  async ready(waitMs = 0): Promise<RuntimeHealth> {
    if (!Number.isFinite(waitMs) || waitMs < 0) throw new Error("waitMs must be nonnegative");
    const deadline = Date.now() + waitMs;
    while (true) {
      try {
        const response = await fetch(new URL("/health", this.baseUrl), {
          signal: AbortSignal.timeout(waitMs ? Math.max(1, Math.min(1000, deadline - Date.now())) : 1000), redirect: "error",
        });
        if (response.status === 503) throw new RuntimeConnectionError("The Decilist runtime is loading. Wait for readiness.");
        if (!response.ok) throw new RuntimeCompatibilityError(`The health endpoint returned HTTP ${response.status}. Check the runtime URL.`);
        let input: unknown;
        try { input = await response.json(); }
        catch { throw new RuntimeCompatibilityError("The endpoint did not return Decilist health JSON. Check the runtime URL."); }
        const health = runtimeHealthSchema.safeParse(input);
        if (!health.success) throw new RuntimeCompatibilityError("The endpoint is not a compatible Decilist runtime. Check its URL and install matching SDK/runtime packages.");
        if (!health.data.ready || health.data.status !== "ok") throw new RuntimeConnectionError("The Decilist runtime is not ready yet.");
        this.isReady = true;
        return health.data;
      } catch (error) {
        if (error instanceof RuntimeCompatibilityError) throw error;
        if (Date.now() >= deadline) throw new RuntimeConnectionError(`Decilist is not ready at ${this.baseUrl}. Start it with: decilist runtime start --provider demo`, {cause: error});
        await new Promise(resolve => setTimeout(resolve, Math.min(100, Math.max(0, deadline - Date.now()))));
      }
    }
  }
  async review(request: ReviewRequest): Promise<unknown> {
    if (!this.isReady) await this.ready();
    let response: Response;
    try {
      response = await fetch(new URL("/v1/code-review", this.baseUrl), {
        method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(request),
        signal:AbortSignal.timeout(this.timeoutMs), redirect:"error",
      });
    } catch (error) {
      this.isReady = false;
      throw new RuntimeConnectionError("Code review connection failed. Check that decilist review serve is running.", {cause:error});
    }
    if (!response.ok) throw new RuntimeConnectionError(`Code review HTTP ${response.status}. Start decilist review serve and connect to its port.`);
    return response.json();
  }
  async decide(question: Question, policy: z.infer<typeof policySchema>): Promise<unknown> {
    if (!this.isReady) await this.ready();
    let response: Response;
    try {
      response = await fetch(new URL("/v1/decisions", this.baseUrl), {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({question, policy}), signal: AbortSignal.timeout(this.timeoutMs), redirect: "error",
      });
    } catch (error) {
      this.isReady = false;
      throw new RuntimeConnectionError(`The Decilist request failed at ${this.baseUrl}. Check that the runtime is still running.`, {cause: error});
    }
    if (!response.ok) throw new RuntimeConnectionError(`Decilist HTTP ${response.status}. Check the runtime and request.`);
    return response.json();
  }
}

export type State = string | Record<string, unknown> | readonly unknown[];

function encodeState(state: State): string {
  if (typeof state === "string") return state;
  const encoded = JSON.stringify(state);
  if (encoded === undefined) throw new TypeError("state must encode to JSON");
  return encoded;
}

export type ConnectOptions = {baseUrl?: string; timeoutMs?: number; waitMs?: number; policy?: Policy};

export class Decilist {
  constructor(readonly transport: Transport = new HttpTransport(), readonly policy: Policy = {}) {}
  async review(code: string, options: ReviewOptions): Promise<ReviewResult> {
    const request = reviewRequestSchema.parse({code,...options});
    if (!this.transport.review) throw new RuntimeCompatibilityError("This transport does not support code review");
    return validateReviewResult(request, await this.transport.review(request));
  }
  /** Configure one endpoint for all direct choose/predict/score calls. */
  static async connect(options: ConnectOptions = {}): Promise<Decilist> {
    const policy = policySchema.parse(options.policy ?? {});
    const transport = new HttpTransport(options.baseUrl, options.timeoutMs);
    await transport.ready(options.waitMs ?? 10000);
    return new Decilist(transport, policy);
  }
  private async execute<T>(question: Question, policy: Policy = this.policy): Promise<Decision<T>> {
    const parsed = questionSchema.parse(question);
    return validateDecision<T>(parsed, await this.transport.decide(parsed, policySchema.parse(policy)));
  }
  decide(question: Question, policy?: Policy): Promise<Decision<string | boolean | number>> {
    return this.execute(question, policy);
  }
  choose<const T extends string>(state: State, instructions: string, options: Record<T, string>, policy?: Policy): Promise<Decision<T>> {
    return this.execute<T>({kind: "choose", state: encodeState(state), instructions, options}, policy);
  }
  /** Ask for every answer, correct it, then apply the threshold here: the runtime does not know the calibration. */
  private async calibrated<T>(question: Question, labels: string[], calibration: Calibration, policy: Policy | undefined, threshold: boolean): Promise<Decision<T>> {
    checkCalibration(calibration, question.kind, labels);
    const used = policySchema.parse(policy ?? this.policy);
    const floor = !threshold ? 0 : policy === undefined && calibration.min_confidence != null ? calibration.min_confidence : used.min_confidence;
    return calibrateDecision(await this.execute<T>(question, {...used, min_confidence: 0}), calibration, floor);
  }
  /** Choose with corrected probabilities. Without a policy here, the calibration's own threshold is used when it has one. */
  chooseCalibrated<const T extends string>(state: State, instructions: string, options: Record<T, string>, config: {calibration: Calibration; policy?: Policy}): Promise<Decision<T>> {
    return this.calibrated<T>({kind: "choose", state: encodeState(state), instructions, options}, Object.keys(options), config.calibration, config.policy, true);
  }
  /** Answer yes or no with corrected probabilities. */
  predictCalibrated(state: State, instructions: string, config: {calibration: Calibration; policy?: Policy}): Promise<Decision<boolean>> {
    return this.calibrated<boolean>({kind: "predict", state: encodeState(state), instructions}, ["false", "true"], config.calibration, config.policy, true);
  }
  /** Check ranked choices. Policy timeout applies to the model, not verify or fallback. */
  async chooseVerified<const T extends string, R = unknown, F = unknown>(
    state: State, instructions: string, options: Record<T, string>,
    config: {verify: (label: T) => R | Promise<R>; fallback?: () => F | Promise<F>; maxTries?: number; policy?: Policy; costs?: Costs; calibration?: Calibration},
  ): Promise<VerifiedDecision<T, R, F>> {
    const maxTries = config.maxTries === undefined ? 3 : config.maxTries;
    if (!Number.isInteger(maxTries) || maxTries < 1) throw new RangeError("maxTries must be an integer of at least 1");
    if (typeof config.verify !== "function") throw new TypeError("verify must be callable");
    if (config.fallback !== undefined && typeof config.fallback !== "function") throw new TypeError("fallback must be callable");
    if (config.costs !== undefined) checkCosts(config.costs, Object.keys(options));
    // With costs the check runs cheapest first, and the confidence threshold is not applied.
    const decision = config.calibration !== undefined
      ? await this.calibrated<T>({kind: "choose", state: encodeState(state), instructions, options}, Object.keys(options), config.calibration, config.policy, config.costs === undefined)
      : await this.choose(state, instructions, options, config.costs === undefined ? config.policy : {...(config.policy ?? this.policy), min_confidence: 0});
    const costed = config.costs === undefined ? undefined : applyCosts(decision, config.costs);
    const extra = costed === undefined ? {} : {costed};
    const tried: TriedOption<T>[] = [];
    if (decision.status === "accepted") {
      const entries = Object.entries(decision.probabilities) as [T, number][];
      entries.sort((a, b) => b[1] - a[1] || Number(b[0] === decision.value) - Number(a[0] === decision.value));
      const ordered = config.costs === undefined ? entries.map(([label]) => label) : rankByCost(decision.probabilities, config.costs).ranked as T[];
      for (const label of ordered.slice(0, maxTries)) {
        const started = performance.now();
        let result: R;
        try {
          result = await config.verify(label) as R;
        } catch (error) {
          tried.push({label, ok: false, latencyMs: performance.now() - started, error: error instanceof Error ? error.name : "Error"});
          continue;
        }
        const ok = Boolean(result);
        tried.push({label, ok, latencyMs: performance.now() - started, error: null});
        if (ok) return {status: "verified", value: label, result, decision, tried, ...extra};
      }
    }
    if (config.fallback !== undefined) return {status: "fallback", value: null, result: await config.fallback() as F, decision, tried, ...extra};
    return {status: "unresolved", value: null, result: null, decision, tried, ...extra};
  }
  /** Choose by expected cost. The confidence threshold is not applied; costs.abstain does that job. */
  async chooseCosted<const T extends string>(state: State, instructions: string, options: Record<T, string>, config: {costs: Costs; policy?: Policy; calibration?: Calibration}): Promise<CostedDecision<T>> {
    checkCosts(config.costs, Object.keys(options));
    if (config.calibration !== undefined) return applyCosts(await this.calibrated<T>({kind: "choose", state: encodeState(state), instructions, options}, Object.keys(options), config.calibration, config.policy, false), config.costs);
    return applyCosts(await this.choose(state, instructions, options, {...(config.policy ?? this.policy), min_confidence: 0}), config.costs);
  }
  /** Answer yes or no by expected cost. Cost labels are "true" and "false". */
  async predictCosted(state: State, instructions: string, config: {costs: Costs; policy?: Policy; calibration?: Calibration}): Promise<CostedDecision<boolean>> {
    checkCosts(config.costs, ["false", "true"]);
    if (config.calibration !== undefined) return applyCosts(await this.calibrated<boolean>({kind: "predict", state: encodeState(state), instructions}, ["false", "true"], config.calibration, config.policy, false), config.costs);
    return applyCosts(await this.predict(state, instructions, {...(config.policy ?? this.policy), min_confidence: 0}), config.costs);
  }
  predict(state: State, instructions: string, policy?: Policy): Promise<Decision<boolean>> {
    return this.execute<boolean>({kind: "predict", state: encodeState(state), instructions}, policy);
  }
  score(state: State, instructions: string, levels: string[], policy?: Policy): Promise<Decision<number>> {
    return this.execute<number>({kind: "score", state: encodeState(state), instructions, levels}, policy);
  }
}
