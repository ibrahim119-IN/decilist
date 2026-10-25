import { z } from "zod";

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

export type ConnectOptions = {baseUrl?: string; timeoutMs?: number; waitMs?: number; policy?: Policy};

export class Decilist {
  constructor(readonly transport: Transport = new HttpTransport(), readonly policy: Policy = {}) {}
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
  choose<const T extends string>(state: string, instructions: string, options: Record<T, string>, policy?: Policy): Promise<Decision<T>> {
    return this.execute<T>({kind: "choose", state, instructions, options}, policy);
  }
  predict(state: string, instructions: string, policy?: Policy): Promise<Decision<boolean>> {
    return this.execute<boolean>({kind: "predict", state, instructions}, policy);
  }
  score(state: string, instructions: string, levels: string[], policy?: Policy): Promise<Decision<number>> {
    return this.execute<number>({kind: "score", state, instructions, levels}, policy);
  }
}
