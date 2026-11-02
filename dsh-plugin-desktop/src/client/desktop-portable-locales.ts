/** Copy shown in the explicit cross-product handoff preview. */
export type DesktopPortableKey = 'action' | 'title' | 'warning' | 'partial' | 'empty' | 'cancel' | 'copy' | 'copied' | 'failed' | 'editor'
export const NS = 'desktop.portable'
export const en: Record<DesktopPortableKey, string> = {
  action: 'Copy context for another AI',
  title: 'Review context before sharing',
  warning: 'This copies text to the clipboard for pasting into another AI. It does not transfer this DSH session, tools, or attachments. Remove secrets and private data before copying.',
  partial: 'This is a partial transcript: only recent loaded messages are included (up to 30 messages / 12,000 characters); replaced messages are omitted.',
  empty: 'No text messages are loaded yet.',
  cancel: 'Cancel',
  copy: 'Copy reviewed text',
  copied: 'Copied',
  failed: 'Clipboard write failed. Check permissions and try again.',
  editor: 'Context to share',
}
export const zh: Record<DesktopPortableKey, string> = {
  action: '复制上下文到其他 AI',
  title: '分享前检查上下文',
  warning: '此操作只复制文本，供粘贴到其他 AI；不会转移 DSH 原会话、工具或附件。复制前请删除密钥和隐私信息。',
  partial: '这不是完整对话：仅包含最近已加载的消息（最多 30 条／12,000 字），被替换的消息会省略。',
  empty: '尚未加载文本消息。',
  cancel: '取消',
  copy: '复制检查后的文本',
  copied: '已复制',
  failed: '写入剪贴板失败，请检查权限后重试。',
  editor: '待分享的上下文',
}
