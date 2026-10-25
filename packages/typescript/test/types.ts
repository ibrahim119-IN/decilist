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
