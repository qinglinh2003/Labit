import assert from "node:assert/strict";
import { copyFile, mkdir, rm } from "node:fs/promises";
import { execFileSync } from "node:child_process";

const outdir = "/tmp/labit-ui-markdown-math-test";
const srcdir = `${outdir}/src`;

await rm(outdir, { recursive: true, force: true });
await mkdir(srcdir, { recursive: true });
await copyFile("src/markdownMath.ts", `${srcdir}/markdownMath.ts`);

execFileSync(
  "./node_modules/.bin/sucrase",
  [srcdir, "--transforms", "typescript", "--out-dir", outdir],
  { stdio: "inherit" },
);

const { normalizeMarkdownMath } = await import(`${outdir}/markdownMath.js`);

assert.equal(
  normalizeMarkdownMath(String.raw`Use \(E=mc^2\) here.`),
  String.raw`Use $E=mc^2$ here.`,
);

assert.equal(
  normalizeMarkdownMath(String.raw`目标分布 (p^\alpha)，提议模型 (p_{\text{prop}})，块大小 (B=192)。`),
  String.raw`目标分布 $p^\alpha$，提议模型 $p_{\text{prop}}$，块大小 $B=192$。`,
);

assert.equal(
  normalizeMarkdownMath(String.raw`Before \[a^2 + b^2 = c^2\] after.`),
  String.raw`Before $$a^2 + b^2 = c^2$$ after.`,
);

assert.equal(
  normalizeMarkdownMath([
    "计算出 (A) 后，随机抽取：",
    "",
    String.raw`[ u\sim\operatorname{Uniform}(0,1) ]`,
    "",
    "如果：",
    "",
    String.raw`[ u\le A ]`,
  ].join("\n")),
  [
    "计算出 (A) 后，随机抽取：",
    "",
    String.raw`$$u\sim\operatorname{Uniform}(0,1)$$`,
    "",
    "如果：",
    "",
    String.raw`$$u\le A$$`,
  ].join("\n"),
);

assert.equal(
  normalizeMarkdownMath([
    "实际在 log 空间计算：",
    "",
    String.raw`[ \log r`,
    String.raw`\alpha\bigl[\log p(x')-\log p(x)\bigr] + \log q(x\mid x')`,
    String.raw`-\log q(x'\mid x) ]`,
  ].join("\n")),
  [
    "实际在 log 空间计算：",
    "",
    String.raw`$$\log r`,
    String.raw`\alpha\bigl[\log p(x')-\log p(x)\bigr] + \log q(x\mid x')`,
    String.raw`-\log q(x'\mid x)$$`,
  ].join("\n"),
);

assert.equal(
  normalizeMarkdownMath("[not a formula]"),
  "[not a formula]",
);

assert.equal(
  normalizeMarkdownMath(String.raw`Already $x_i$ and $$\sum_i x_i$$.`),
  String.raw`Already $x_i$ and $$\sum_i x_i$$.`,
);

assert.equal(
  normalizeMarkdownMath(String.raw`Inline code \`\(not math\)\` should stay.`),
  String.raw`Inline code \`\(not math\)\` should stay.`,
);

assert.equal(
  normalizeMarkdownMath([
    "Fenced:",
    "```tex",
    String.raw`\[not math\]`,
    "```",
    "Done.",
  ].join("\n")),
  [
    "Fenced:",
    "```tex",
    String.raw`\[not math\]`,
    "```",
    "Done.",
  ].join("\n"),
);

console.log("markdown math tests passed");
