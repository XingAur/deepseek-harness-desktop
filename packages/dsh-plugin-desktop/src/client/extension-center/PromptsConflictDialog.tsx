import { useState } from 'react'
import { TARGET_LABELS, type PromptTarget } from './prompts-api'

export interface ConflictCandidateView { target: PromptTarget; content: string; updatedAt: number }

export function PromptsConflictDialog(props: {
  presetTitle: string
  candidates: ConflictCandidateView[]
  onClose(): void
  onResolve(chosen: ConflictCandidateView): void
}) {
  const [chosen, setChosen] = useState<ConflictCandidateView | null>(null)
  return (
    <div className="dshPromptsDialogBackdrop" role="presentation">
      <div className="dshPromptsDialog" role="dialog" aria-label="检测到外部修改">
        <h3>检测到外部修改</h3>
        <p>「{props.presetTitle}」在多个目标上的文件内容互不一致,选择以哪份为准:</p>
        {props.candidates.map((candidate) => (
          <label key={candidate.target} className="dshPromptsImportRow">
            <input
              type="radio"
              name="prompts-conflict-candidate"
              aria-label={`${TARGET_LABELS[candidate.target]}(更新于 ${new Date(candidate.updatedAt).toLocaleString()})`}
              checked={chosen?.target === candidate.target}
              onChange={() => setChosen(candidate)}
            />
            <span>{TARGET_LABELS[candidate.target]}</span>
            <span className="dshPromptsMuted">{new Date(candidate.updatedAt).toLocaleString()}</span>
          </label>
        ))}
        <div className="dshPromptsDialogActions">
          <button type="button" onClick={props.onClose}>取消</button>
          <button type="button" disabled={chosen === null} onClick={() => { if (chosen !== null) props.onResolve(chosen) }}>
            以此为准并保存
          </button>
        </div>
      </div>
    </div>
  )
}
