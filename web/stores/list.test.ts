import { describe, expect, it } from "vitest";
import { neighbours } from "./list";

describe("neighbours", () => {
  it("finds the previous and next stock in the list", () => {
    expect(neighbours(["A", "B", "C"], "B")).toEqual(["A", "C"]);
    expect(neighbours(["A", "B", "C"], "A")).toEqual([null, "B"]);
    expect(neighbours(["A", "B", "C"], "C")).toEqual(["B", null]);
    expect(neighbours(["A", "B"], "Z")).toEqual([null, null]);
  });
});
