/**
 * Minimal markdown renderer for plan documents: headings, lists, quotes,
 * fenced code, inline code, and bold — everything plan markdown needs with
 * zero dependencies and CSP-safe static markup.
 */

import type { ReactNode } from 'react'

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = []
  const pattern = /(`[^`]+`)|(\*\*[^*]+\*\*)/gu
  let lastIndex = 0
  let match: RegExpExecArray | null
  let index = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index))
    const token = match[0]
    const key = `${keyPrefix}-i${String(index)}`
    index += 1
    if (token.startsWith('`')) {
      nodes.push(<code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-foreground" key={key}>{token.slice(1, -1)}</code>)
    } else {
      nodes.push(<strong className="font-semibold text-foreground" key={key}>{token.slice(2, -2)}</strong>)
    }
    lastIndex = match.index + token.length
  }
  if (lastIndex < text.length) nodes.push(text.slice(lastIndex))
  return nodes
}

interface Block {
  readonly kind: 'p' | 'h1' | 'h2' | 'h3' | 'quote' | 'ul' | 'ol' | 'code' | 'hr'
  readonly lines: readonly string[]
  readonly language: string
}

function parseBlocks(markdown: string): readonly Block[] {
  const blocks: Block[] = []
  const lines = markdown.split('\n')
  let index = 0
  while (index < lines.length) {
    const line = lines[index] ?? ''
    if (line.trim() === '') {
      index += 1
      continue
    }
    if (line.startsWith('```')) {
      const language = line.slice(3).trim()
      const body: string[] = []
      index += 1
      while (index < lines.length && !(lines[index] ?? '').startsWith('```')) {
        body.push(lines[index] ?? '')
        index += 1
      }
      index += 1
      blocks.push({ kind: 'code', lines: body, language })
      continue
    }
    if (/^---+\s*$/u.test(line)) {
      blocks.push({ kind: 'hr', lines: [], language: '' })
      index += 1
      continue
    }
    const heading = /^(#{1,3})\s+(.*)$/u.exec(line)
    if (heading !== null) {
      const depth = heading[1]?.length ?? 1
      blocks.push({ kind: depth === 1 ? 'h1' : depth === 2 ? 'h2' : 'h3', lines: [heading[2] ?? ''], language: '' })
      index += 1
      continue
    }
    if (line.startsWith('> ')) {
      const body: string[] = []
      while (index < lines.length && (lines[index] ?? '').startsWith('> ')) {
        body.push((lines[index] ?? '').slice(2))
        index += 1
      }
      blocks.push({ kind: 'quote', lines: body, language: '' })
      continue
    }
    if (/^[-*]\s+/u.test(line)) {
      const body: string[] = []
      while (index < lines.length && /^[-*]\s+/u.test(lines[index] ?? '')) {
        body.push((lines[index] ?? '').replace(/^[-*]\s+/u, ''))
        index += 1
      }
      blocks.push({ kind: 'ul', lines: body, language: '' })
      continue
    }
    if (/^\d+[.)]\s+/u.test(line)) {
      const body: string[] = []
      while (index < lines.length && /^\d+[.)]\s+/u.test(lines[index] ?? '')) {
        body.push((lines[index] ?? '').replace(/^\d+[.)]\s+/u, ''))
        index += 1
      }
      blocks.push({ kind: 'ol', lines: body, language: '' })
      continue
    }
    const body: string[] = []
    while (index < lines.length && (lines[index] ?? '').trim() !== '') {
      body.push(lines[index] ?? '')
      index += 1
    }
    blocks.push({ kind: 'p', lines: body, language: '' })
  }
  return blocks
}

export function MarkdownView({ markdown }: { readonly markdown: string }): ReactNode {
  const blocks = parseBlocks(markdown)
  return <div className="flex flex-col gap-2.5 text-[13px] leading-5 text-foreground/90">
    {blocks.map((block, index) => {
      const key = `b${String(index)}`
      const text = block.lines.join('\n')
      switch (block.kind) {
        case 'h1':
          return <h1 className="pt-1 text-base font-bold text-foreground" key={key}>{renderInline(text, key)}</h1>
        case 'h2':
          return <h2 className="pt-1 text-[15px] font-bold text-foreground" key={key}>{renderInline(text, key)}</h2>
        case 'h3':
          return <h3 className="pt-0.5 text-sm font-semibold text-foreground" key={key}>{renderInline(text, key)}</h3>
        case 'quote':
          return <blockquote className="border-l-2 border-border pl-3 text-muted-foreground" key={key}>{renderInline(text, key)}</blockquote>
        case 'ul':
          return <ul className="flex flex-col gap-1 pl-1" key={key}>
            {block.lines.map((row, itemIndex) => <li className="flex gap-2" key={`${key}-${String(itemIndex)}`}>
              <span aria-hidden className="mt-[7px] size-1.5 shrink-0 rounded-full bg-primary/60" />
              <span className="min-w-0 flex-1">{renderInline(row, `${key}-${String(itemIndex)}`)}</span>
            </li>)}
          </ul>
        case 'ol':
          return <ol className="flex flex-col gap-1 pl-1" key={key}>
            {block.lines.map((row, itemIndex) => <li className="flex gap-2" key={`${key}-${String(itemIndex)}`}>
              <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-semibold tabular-nums text-muted-foreground">{String(itemIndex + 1)}</span>
              <span className="min-w-0 flex-1">{renderInline(row, `${key}-${String(itemIndex)}`)}</span>
            </li>)}
          </ol>
        case 'code':
          return <pre className="overflow-x-auto rounded-lg bg-muted/70 px-3 py-2.5 font-mono text-xs leading-5 text-foreground/90" key={key}>{text}</pre>
        case 'hr':
          return <hr className="border-border/70" key={key} />
        default:
          return <p className="whitespace-pre-wrap break-words" key={key}>{renderInline(text, key)}</p>
      }
    })}
  </div>
}
