function splitMarkdownCodeSegments(input: string): Array<{ text: string; code: boolean }> {
  const segments: Array<{ text: string; code: boolean }> = [];
  let text = "";
  let code = "";
  let inFence = false;
  let inInlineCode = false;

  for (let i = 0; i < input.length; i += 1) {
    if (!inInlineCode && input.startsWith("```", i)) {
      if (inFence) {
        code += "```";
        segments.push({ text: code, code: true });
        code = "";
        inFence = false;
      } else {
        if (text) segments.push({ text, code: false });
        text = "";
        code = "```";
        inFence = true;
      }
      i += 2;
      continue;
    }

    if (!inFence && input[i] === "`") {
      if (inInlineCode) {
        code += "`";
        segments.push({ text: code, code: true });
        code = "";
        inInlineCode = false;
      } else {
        if (text) segments.push({ text, code: false });
        text = "";
        code = "`";
        inInlineCode = true;
      }
      continue;
    }

    if (inFence || inInlineCode) {
      code += input[i];
    } else {
      text += input[i];
    }
  }

  if (code) segments.push({ text: code, code: true });
  if (text) segments.push({ text, code: false });
  return segments;
}

function normalizeMathDelimiters(text: string): string {
  const normalizedBlocks = text
    .replace(/\\\[([\s\S]+?)\\\]/g, (_match, math: string) => `$$${math.trim()}$$`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_match, math: string) => `$${math.trim()}$`)
    .replace(
      /(^|\n)([ \t]*)\[\s*([\s\S]*?)\s*\]([ \t]*)(?=\n|$)/g,
      (match, prefix: string, indent: string, math: string, trailing: string) => {
        const value = math.trim();
        if (!looksLikeMath(value)) return match;
        return `${prefix}${indent}$$${value}$$${trailing}`;
      },
    );

  return splitDollarMathSegments(normalizedBlocks)
    .map((segment) => segment.math ? segment.text : normalizeParenthesizedMath(segment.text))
    .join("");
}

function looksLikeMath(value: string): boolean {
  return /\\|[=<>^_]|(?:^|[^\p{L}])(?:alpha|tau|log|min|max|sum|frac|operatorname)(?:$|[^\p{L}])/u.test(value);
}

function normalizeParenthesizedMath(text: string): string {
  return text.replace(/\(([^()\n]+)\)/g, (match, math: string) => {
    const value = math.trim();
    if (!looksLikeMath(value)) return match;
    return `$${value}$`;
  });
}

function splitDollarMathSegments(input: string): Array<{ text: string; math: boolean }> {
  const segments: Array<{ text: string; math: boolean }> = [];
  let text = "";

  for (let i = 0; i < input.length; i += 1) {
    const delimiter = input.startsWith("$$", i) ? "$$" : input[i] === "$" ? "$" : null;
    if (!delimiter) {
      text += input[i];
      continue;
    }

    const end = input.indexOf(delimiter, i + delimiter.length);
    if (end === -1) {
      text += input[i];
      continue;
    }

    if (text) segments.push({ text, math: false });
    segments.push({ text: input.slice(i, end + delimiter.length), math: true });
    text = "";
    i = end + delimiter.length - 1;
  }

  if (text) segments.push({ text, math: false });
  return segments;
}

export function normalizeMarkdownMath(input: string): string {
  return splitMarkdownCodeSegments(input)
    .map((segment) => segment.code ? segment.text : normalizeMathDelimiters(segment.text))
    .join("");
}
