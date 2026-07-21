import { Fragment, type ReactNode } from "react";
import { safeUrl } from "./url";

/**
 * A deliberately small markdown renderer — enough for the studio proposals
 * (headings, bold, italic, inline code, links, lists, rules, paragraphs).
 * No dependency, offline, $0. Not a general-purpose parser.
 */
export function Markdown({ text }: { text: string }) {
  return <div className="markdown">{renderBlocks(text)}</div>;
}

function renderBlocks(src: string): ReactNode[] {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let list: string[] | null = null;
  let para: string[] = [];
  let key = 0;

  const flushPara = () => {
    if (para.length) {
      out.push(<p key={key++}>{inline(para.join(" "))}</p>);
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      const items = list;
      out.push(
        <ul key={key++}>
          {items.map((li, i) => (
            <li key={i}>{inline(li)}</li>
          ))}
        </ul>,
      );
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^#{1,3}\s+/.test(line)) {
      flushPara();
      flushList();
      const level = line.match(/^(#{1,3})/)![1].length;
      const content = line.replace(/^#{1,3}\s+/, "");
      const H = `h${level}` as "h1" | "h2" | "h3";
      out.push(<H key={key++}>{inline(content)}</H>);
    } else if (/^[-*]\s+/.test(line)) {
      flushPara();
      (list ??= []).push(line.replace(/^[-*]\s+/, ""));
    } else if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      flushPara();
      flushList();
      out.push(<hr key={key++} />);
    } else if (line.trim() === "") {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return out;
}

function inline(text: string): ReactNode {
  // order matters: code first so we don't format inside it
  const nodes: ReactNode[] = [];
  const regex = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = regex.exec(text))) {
    if (m.index > last) nodes.push(<Fragment key={key++}>{text.slice(last, m.index)}</Fragment>);
    const tok = m[0];
    if (tok.startsWith("`")) nodes.push(<code key={key++}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("**")) nodes.push(<strong key={key++}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("*")) nodes.push(<em key={key++}>{tok.slice(1, -1)}</em>);
    else {
      const lm = tok.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (lm) {
        // markdown here is agent-authored (proposals, blueprints) — never trust
        // the href scheme; a `javascript:` link would run in the hub's origin.
        const href = safeUrl(lm[2]);
        nodes.push(
          href ? (
            <a key={key++} href={href} target="_blank" rel="noreferrer">
              {lm[1]}
            </a>
          ) : (
            <Fragment key={key++}>{lm[1]}</Fragment>
          ),
        );
      }
    }
    last = regex.lastIndex;
  }
  if (last < text.length) nodes.push(<Fragment key={key}>{text.slice(last)}</Fragment>);
  return nodes;
}
