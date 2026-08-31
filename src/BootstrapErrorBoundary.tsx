import { Component, type ErrorInfo, type ReactNode } from 'react'
import { removeBootstrapFallback } from './bootstrap-fallback'

interface BootstrapErrorBoundaryProps {
  children: ReactNode
}

interface BootstrapErrorBoundaryState {
  error: Error | null
}

export class BootstrapErrorBoundary extends Component<BootstrapErrorBoundaryProps, BootstrapErrorBoundaryState> {
  state: BootstrapErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): BootstrapErrorBoundaryState {
    return { error }
  }

  componentDidMount() {
    removeBootstrapFallback()
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('DeepSeek Harness renderer failed during startup', error, info.componentStack)
  }

  render() {
    if (this.state.error === null) return this.props.children

    return (
      <main
        role="alert"
        style={{
          boxSizing: 'border-box',
          width: '100%',
          height: '100%',
          padding: '32px',
          color: '#f7f7f8',
          background: '#111113',
          fontFamily: 'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        }}
      >
        <h1 style={{ margin: '0 0 12px', fontSize: '20px' }}>DeepSeek Harness 启动失败</h1>
        <p style={{ margin: '0 0 20px', color: '#a7a7ae', lineHeight: 1.6 }}>
          工作台没有正常加载。请重新打开应用；如果仍然出现，请导出诊断信息并反馈下面的错误。
        </p>
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', color: '#ffb4b4', font: '12px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace' }}>
          {this.state.error.message}
        </pre>
      </main>
    )
  }
}
