const SCALE = 1_000_000n;

function parsePercentScaled(value) {
  const normalized = String(value).trim().replace(",", ".");
  const match = /^(\d+)(?:\.(\d{1,6}))?$/.exec(normalized);
  if (!match) throw new Error("Введите ставку от 0 до 100% не более чем с 6 знаками после запятой.");
  const whole = BigInt(match[1]);
  const fraction = BigInt((match[2] || "").padEnd(6, "0"));
  const scaled = whole * SCALE + fraction;
  if (scaled > 100n * SCALE) throw new Error("Ставка должна быть от 0 до 100%.");
  return scaled;
}

export function percentToFraction(value) {
  const scaledPercent = parsePercentScaled(value);
  const digits = scaledPercent.toString().padStart(9, "0");
  const fraction = digits.slice(0, -8);
  const decimal = digits.slice(-8).replace(/0+$/, "");
  return decimal ? `${fraction}.${decimal}` : fraction;
}

export function fractionToPercent(value) {
  const normalized = String(value).trim();
  const match = /^(\d+)(?:\.(\d{1,8}))?$/.exec(normalized);
  if (!match) throw new Error("Недопустимая доля ставки в сохранённой сделке.");
  const scaledPercent = BigInt(match[1]) * 100n * SCALE + BigInt((match[2] || "").padEnd(8, "0"));
  const whole = scaledPercent / SCALE;
  const decimal = (scaledPercent % SCALE).toString().padStart(6, "0").replace(/0+$/, "");
  return decimal ? `${whole}.${decimal}` : whole.toString();
}

export function addPercentagePoints(value, points) {
  const scaled = parsePercentScaled(value) + BigInt(points) * SCALE;
  if (scaled < 0n || scaled > 100n * SCALE) throw new Error("Ставки серии должны быть от 0 до 100%.");
  return percentToFraction(`${scaled / SCALE}.${(scaled % SCALE).toString().padStart(6, "0")}`);
}
