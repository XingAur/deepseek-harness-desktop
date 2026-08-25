export function removeOwnedTreeWithoutFollowingReparsePoints(
  root: string,
  options?: {
    beforeEnumerate?: (path: string) => void | Promise<void>
    beforeScheduleDirectChildren?: (path: string) => void | Promise<void>
    onLstat?: (path: string) => void
    onOperationEnd?: () => void
    onOperationStart?: () => void
  },
): Promise<void>
