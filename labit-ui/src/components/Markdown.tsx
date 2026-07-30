import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { normalizeMarkdownMath } from "../markdownMath";

const markdownClassName = `labit-markdown prose prose-sm prose-slate max-w-none break-words
  prose-p:my-1.5 prose-p:leading-relaxed
  prose-headings:mt-3 prose-headings:mb-1.5 prose-headings:font-semibold
  prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0.5
  prose-pre:my-2 prose-pre:rounded-lg prose-pre:bg-slate-800 prose-pre:text-slate-100
  prose-code:rounded prose-code:bg-slate-100 prose-code:px-1 prose-code:py-0.5 prose-code:text-slate-800 prose-code:before:content-none prose-code:after:content-none
  prose-table:text-sm prose-th:px-2 prose-th:py-1 prose-td:px-2 prose-td:py-1
  prose-blockquote:border-slate-300 prose-blockquote:text-slate-600
  prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline
  prose-img:rounded-lg`;

export default function Markdown({ children }: { children: string }) {
  return (
    <div className={markdownClassName}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
      >
        {normalizeMarkdownMath(children)}
      </ReactMarkdown>
    </div>
  );
}
