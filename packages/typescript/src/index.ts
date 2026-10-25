import { z } from "zod";

const text = z.string().trim().min(1).max(65536);
const label = z.string().min(1).max(256).refine(value => value === value.trim());
const base = { state: text, instructions: text };
export const questionSchema = z.discriminatedUnion("kind", [
  z.strictObject({ ...base, kind: z.literal("choose"), options: z.record(label, text).refine(x => Object.keys(x).length >= 2 && Object.keys(x).length <= 64) }),
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
  probabilities: z.record(z.string(), z.number().min(0).max(1)),
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

export class HttpTransport implements Transport {
  constructor(readonly baseUrl = "http://127.0.0.1:8765", readonly timeoutMs = 130000) {
    const url = new URL(baseUrl);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Invalid API URL");
  }
  async decide(question: Question, policy: z.infer<typeof policySchema>): Promise<unknown> {
    const response = await fetch(new URL("/v1/decisions", this.baseUrl), {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question, policy}), signal: AbortSignal.timeout(this.timeoutMs), redirect: "error",
    });
    if (!response.ok) throw new Error(`Decilist HTTP ${response.status}`);
    return response.json();
  }
}

export class Decilist {
  constructor(readonly transport: Transport = new HttpTransport(), readonly policy: Policy = {}) {}
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
