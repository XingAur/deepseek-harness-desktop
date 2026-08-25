export async function closeCleanupAndStage(input: {
  close(): Promise<void>
  cleanup(): Promise<void>
  stage(): void
}): Promise<void> {
  const failures: unknown[] = []
  try {
    await input.close()
  } catch (error) {
    failures.push(error)
  }
  try {
    await input.cleanup()
  } catch (error) {
    failures.push(error)
  }
  try {
    input.stage()
  } catch (error) {
    failures.push(error)
  }
  if (failures.length === 1) throw failures[0]
  if (failures.length > 1) throw new AggregateError(failures, 'E2E 生命周期收尾失败')
}
