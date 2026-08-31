import { Component, type ReactNode } from 'react'

interface Props { label: string; children: ReactNode }
interface State { error: Error | null }

export class ExtensionErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error): void {
    console.error(`[desktop-plugin] ${this.props.label} 渲染崩溃:`, error)
  }

  private reset = () => { this.setState({ error: null }) }

  render(): ReactNode {
    if (this.state.error !== null) {
      return (
        <section className="dshExtErrorCard" role="alert">
          <h3>{this.props.label} 暂时不可用</h3>
          <p>渲染时发生错误,不影响其他功能。可点击重试;若持续出现,请通过「设置 → 诊断」导出诊断信息。</p>
          <button type="button" onClick={this.reset}>重试</button>
        </section>
      )
    }
    return this.props.children
  }
}
