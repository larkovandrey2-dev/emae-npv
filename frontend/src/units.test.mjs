import test from "node:test";
import assert from "node:assert/strict";
import {
  percentToFraction,
  fractionToPercent,
  addPercentagePoints,
} from "./units.js";

test("percentage conversion is decimal-exact for API precision", () => {
  assert.equal(percentToFraction("9.9"), "0.099");
  assert.equal(percentToFraction("11.9"), "0.119");
  assert.equal(percentToFraction("7.426415"), "0.07426415");
});

test("imported rate precision is preserved in percent presets", () => {
  assert.equal(fractionToPercent("0.07426415"), "7.426415");
  assert.equal(addPercentagePoints("7.426415", 0), "0.07426415");
  assert.equal(
    fractionToPercent(addPercentagePoints("7.426415", 1)),
    "8.426415",
  );
});

test("invalid percent precision and bounds are rejected", () => {
  assert.throws(() => percentToFraction("9.1234567"), /6 знаками/);
  assert.throws(() => percentToFraction("-0.1"), /6 знаками/);
  assert.throws(() => percentToFraction("100.01"), /от 0 до 100/);
  assert.throws(() => addPercentagePoints("0.5", -1), /от 0 до 100/);
});
