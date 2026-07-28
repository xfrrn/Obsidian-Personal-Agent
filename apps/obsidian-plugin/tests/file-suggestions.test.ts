import assert from "node:assert/strict";
import test from "node:test";
import { fileSuggestionIndex } from "../ui/src/file-suggestions";

test("arrow keys move and wrap the active file suggestion", () => {
  assert.equal(fileSuggestionIndex(0, 3, "ArrowDown"), 1);
  assert.equal(fileSuggestionIndex(2, 3, "ArrowDown"), 0);
  assert.equal(fileSuggestionIndex(0, 3, "ArrowUp"), 2);
});

test("Enter selects the active suggestion without inventing an empty result", () => {
  assert.equal(fileSuggestionIndex(1, 3, "Enter"), 1);
  assert.equal(fileSuggestionIndex(0, 0, "Enter"), null);
});
