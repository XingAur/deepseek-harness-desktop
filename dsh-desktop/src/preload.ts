import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('dsh', {
  listProjects: () => ipcRenderer.invoke('projects:list'),
  open: (target: string) => ipcRenderer.invoke('open', target),
  retry: () => ipcRenderer.invoke('retry'),
  state: () => ipcRenderer.invoke('state'),
})
