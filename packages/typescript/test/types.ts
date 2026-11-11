import { Decilist } from "../src/index.js";
const sdk = new Decilist();
const choice = await sdk.choose("state", "Choose", {proceed:"Ready", review:"Needs review"});
if (choice.status === "accepted") {
  const selected: "proceed" | "review" = choice.value;
  // @ts-expect-error an arbitrary category must not escape the declared options
  const invalid: "other" = choice.value;
}
const predicate = await sdk.predict("state", "Ready?");
if (predicate.status === "accepted") {
  const yes: boolean = predicate.value;
  // @ts-expect-error a predicate must not be typed as a choice
  const invalid: string = predicate.value;
}
const score = await sdk.score("state", "Rate", ["Low", "High"]);
if (score.status === "accepted") {
  const numeric: number = score.value;
}
const special = await sdk.choose("state", "Choose", {["__proto__"]:"Prototype label",constructor:"Constructor label"});
if (special.status === "accepted") {
  const selected: "__proto__" | "constructor" = special.value;
  // @ts-expect-error special labels must retain the same bounded choice type
  const invalid: "other" = special.value;
}

await sdk.choose({count: 1}, "Choose", {yes: "Yes", no: "No"});
await sdk.predict([1, "two"] as const, "Ready?");
await sdk.score({nested: [true]}, "Rate", ["Low", "High"]);
const checked = await sdk.chooseVerified("state", "Choose", {proceed:"Ready", review:"Review"}, {
  verify: label => label === "proceed" ? {ticket: 1} : null,
  fallback: () => "manual" as const,
});
if (checked.status === "verified") {
  const selected: "proceed" | "review" = checked.value;
  const result: {ticket: number} | null = checked.result;
  // @ts-expect-error labels are restricted to the options
  const invalid: "other" = checked.value;
} else if (checked.status === "fallback") {
  const fallback: "manual" = checked.result;
  const value: null = checked.value;
} else {
  const result: null = checked.result;
  const value: null = checked.value;
}
// @ts-expect-error primitive numeric state is not supported
await sdk.predict(42, "Ready?");

const costed = await sdk.chooseCosted("state", "Choose", {proceed:"Ready", review:"Review"}, {costs: {wrong: {proceed: {review: 10}}, abstain: 2}});
if (costed.status === "accepted") {
  const selected: "proceed" | "review" = costed.value;
  const cost: number = costed.expectedCost;
  // @ts-expect-error a costed choice keeps the declared options
  const invalid: "other" = costed.value;
} else {
  const none: null = costed.value;
}
const costedPredicate = await sdk.predictCosted("state", "Ready?", {costs: {abstain: null}});
if (costedPredicate.status === "accepted") {
  const yes: boolean = costedPredicate.value;
}
// @ts-expect-error abstain is required so the cost of asking a person is always stated
await sdk.chooseCosted("state", "Choose", {proceed:"Ready", review:"Review"}, {costs: {wrong: {}}});
