import { z } from "zod";

export const reviewRuleSchema = z.enum(["unclear_naming", "noise_comments", "dead_code", "inconsistent_style", "over_abstracted", "too_clever", "duplicated_logic", "error_handling_problem", "type_safety_hole", "unsafe_input"]);
export const reviewPolicySchema = z.strictObject({
  rules: z.array(reviewRuleSchema).min(1).max(10).refine(x => new Set(x).size === x.length).default(["error_handling_problem", "type_safety_hole", "unsafe_input"]),
  threshold: z.number().min(0.5).max(1).default(0.9),
  timeout_ms: z.number().int().min(1).max(120000).default(30000),
});
function lineCount(code: string): number {
  const lines = code.split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/);
  if (lines.at(-1) === "") lines.pop();
  return lines.length;
}
export const reviewRequestSchema = z.strictObject({
  code: z.string().min(1).max(65536).refine(x => !!x.trim() && !x.includes("\0") && lineCount(x) <= 2000),
  task: z.string().trim().min(1).max(2000),
  language: z.enum(["JavaScript", "JSX", "TypeScript", "TSX"]).default("TypeScript"),
  policy: reviewPolicySchema.default({rules:["error_handling_problem", "type_safety_hole", "unsafe_input"],threshold:0.9,timeout_ms:30000}),
});
const findingSchema = z.strictObject({rule:reviewRuleSchema, probability:z.number().min(0).max(1), start_line:z.number().int().positive(), end_line:z.number().int().positive()});
export const reviewResultSchema = z.strictObject({
  schema_version:z.literal("code-review.1"),status:z.enum(["flagged","no_flags","unavailable"]),advisory:z.literal(true),
  model:z.string().min(1),revision:z.string().min(1),rules:z.array(reviewRuleSchema).min(1).max(10),threshold:z.number().min(0.5).max(1),
  total_lines:z.number().int().min(1).max(2000),reviewed_lines:z.number().int().min(0).max(2000),
  windows:z.array(z.strictObject({start_line:z.number().int().positive(),end_line:z.number().int().positive(),probabilities:z.record(z.string(),z.number().min(0).max(1))})),
  findings:z.array(findingSchema),latency_ms:z.number().min(0),error:z.enum(["timeout","invalid_output","provider_error"]).nullable(),
});
export type ReviewPolicy = z.input<typeof reviewPolicySchema>;
export type ReviewOptions = Omit<z.input<typeof reviewRequestSchema>, "code">;
export type ReviewRequest = z.output<typeof reviewRequestSchema>;
export type ReviewResult = z.output<typeof reviewResultSchema>;

export function validateReviewResult(request: ReviewRequest, input: unknown): ReviewResult {
  const result = reviewResultSchema.parse(input);
  if (JSON.stringify(result.rules) !== JSON.stringify(request.policy.rules) || result.threshold !== request.policy.threshold || result.total_lines !== lineCount(request.code)) throw new Error("Review response does not match the request");
  const covered = new Set<number>();
  const findings: z.output<typeof findingSchema>[] = [];
  result.windows.forEach((window, index) => {
    const start = index * 68 + 1, end = Math.min(start + 79, result.total_lines);
    if (covered.size === result.total_lines || start > result.total_lines || window.start_line !== start || window.end_line !== end || Object.keys(window.probabilities).length !== result.rules.length || result.rules.some(rule => !Object.hasOwn(window.probabilities,rule))) throw new Error("Invalid review coverage or rules");
    for (let line = start; line <= end; line++) covered.add(line);
    for (const rule of result.rules) if (window.probabilities[rule]! >= result.threshold) findings.push({rule,probability:window.probabilities[rule]!,start_line:start,end_line:end});
  });
  if (covered.size !== result.reviewed_lines || JSON.stringify(result.findings) !== JSON.stringify(findings)) throw new Error("Review findings do not match probabilities");
  const status = result.error ? "unavailable" : findings.length ? "flagged" : "no_flags";
  if (result.status !== status || (!result.error && result.reviewed_lines !== result.total_lines)) throw new Error("Incomplete review cannot be reported as completed");
  return result;
}
