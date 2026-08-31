# 阶段4：UI/UX 升级设计与实现文档

## 📋 文档信息

| 项目 | 内容 |
|------|------|
| **阶段** | 阶段4：UI/UX 升级 |
| **版本** | v1.0.0 |
| **创建日期** | 2026-08-15 |
| **状态** | 设计中 |
| **预计工期** | 4-6 周 |

## 🎯 阶段目标

将现有的静态 HTML 工作台升级为现代化的 Web 应用，提供可视化技能包市场、拖拽式工作流编辑器、实时协作和监控能力。

## 🏗️ 架构设计

### 1. 前端技术栈选择

| 维度 | 方案 A (React) | 方案 B (Vue) | 推荐 |
|------|---------------|-------------|------|
| 生态丰富度 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | React |
| 团队熟悉度 | 视团队而定 | 视团队而定 | 按团队 |
| 可视化图表 | React Flow, D3 | Vue Flow, ECharts | React Flow |
| 状态管理 | Redux/Zustand | Pinia/Vuex | Zustand |
| 构建工具 | Vite | Vite | Vite |
| TypeScript | 一流支持 | 一流支持 | 两者 |

**推荐方案**：React 18 + TypeScript + Vite + React Flow（工作流编辑器）+ Zustand（状态管理）

### 2. 前端项目结构

```
ui/
├── src/                              # 源代码
│   ├── main.tsx                     # 入口文件
│   ├── App.tsx                      # 根组件
│   ├── router.tsx                   # 路由配置
│   ├── theme.ts                     # 主题配置
│   │
│   ├── components/                  # 通用组件
│   │   ├── layout/                 # 布局组件
│   │   │   ├── AppLayout.tsx
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Header.tsx
│   │   │   ├── Footer.tsx
│   │   │   └── Breadcrumb.tsx
│   │   │
│   │   ├── common/                 # 通用组件
│   │   │   ├── Button.tsx
│   │   │   ├── Modal.tsx
│   │   │   ├── Card.tsx
│   │   │   ├── Table.tsx
│   │   │   ├── Tabs.tsx
│   │   │   ├── Tag.tsx
│   │   │   ├── Tooltip.tsx
│   │   │   ├── Dropdown.tsx
│   │   │   ├── SearchInput.tsx
│   │   │   └── StatusBadge.tsx
│   │   │
│   │   ├── charts/                 # 图表组件
│   │   │   ├── LineChart.tsx
│   │   │   ├── BarChart.tsx
│   │   │   ├── PieChart.tsx
│   │   │   ├── GaugeChart.tsx
│   │   │   └── HeatmapChart.tsx
│   │   │
│   │   └── feedback/               # 反馈组件
│   │       ├── Toast.tsx
│   │       ├── Alert.tsx
│   │       ├── Loading.tsx
│   │       ├── Skeleton.tsx
│   │       └── EmptyState.tsx
│   │
│   ├── pages/                       # 页面组件
│   │   ├── Dashboard/              # 仪表盘
│   │   │   ├── index.tsx
│   │   │   ├── OverviewCard.tsx
│   │   │   ├── ActivityFeed.tsx
│   │   │   ├── QuickActions.tsx
│   │   │   └── RecentRuns.tsx
│   │   │
│   │   ├── Workflows/              # 工作流管理
│   │   │   ├── index.tsx
│   │   │   ├── WorkflowList.tsx
│   │   │   ├── WorkflowDetail.tsx
│   │   │   ├── WorkflowCreate.tsx
│   │   │   └── WorkflowRun.tsx
│   │   │
│   │   ├── WorkflowEditor/         # 工作流编辑器
│   │   │   ├── index.tsx
│   │   │   ├── Canvas.tsx
│   │   │   ├── NodePalette.tsx
│   │   │   ├── PropertyPanel.tsx
│   │   │   ├── EdgeConfig.tsx
│   │   │   └── ValidationPanel.tsx
│   │   │
│   │   ├── SkillPack/              # 技能包市场
│   │   │   ├── index.tsx
│   │   │   ├── SkillPackList.tsx
│   │   │   ├── SkillPackDetail.tsx
│   │   │   ├── SkillPackInstall.tsx
│   │   │   └── SkillPackManage.tsx
│   │   │
│   │   ├── RulePack/               # 规则包管理
│   │   │   ├── index.tsx
│   │   │   ├── RulePackList.tsx
│   │   │   ├── RulePackDetail.tsx
│   │   │   ├── RulePackEditor.tsx
│   │   │   └── RulePackCompare.tsx
│   │   │
│   │   ├── Workspace/              # 工作空间管理
│   │   │   ├── index.tsx
│   │   │   ├── WorkspaceList.tsx
│   │   │   ├── WorkspaceDetail.tsx
│   │   │   └── WorkspaceSettings.tsx
│   │   │
│   │   ├── Monitoring/             # 监控中心
│   │   │   ├── index.tsx
│   │   │   ├── RunMonitor.tsx
│   │   │   ├── PerformanceMonitor.tsx
│   │   │   ├── ErrorMonitor.tsx
│   │   │   └── AuditLog.tsx
│   │   │
│   │   └── Settings/               # 设置
│   │       ├── index.tsx
│   │       ├── ProfileSettings.tsx
│   │       ├── IntegrationSettings.tsx
│   │       └── SecuritySettings.tsx
│   │
│   ├── hooks/                       # 自定义 Hooks
│   │   ├── useWorkflow.ts
│   │   ├── useSkillPack.ts
│   │   ├── useRulePack.ts
│   │   ├── useWorkspace.ts
│   │   ├── useWebSocket.ts
│   │   ├── useDebounce.ts
│   │   ├── useLocalStorage.ts
│   │   └── usePermission.ts
│   │
│   ├── services/                    # API 服务层
│   │   ├── api.ts                  # API 客户端
│   │   ├── workflowService.ts
│   │   ├── skillPackService.ts
│   │   ├── rulePackService.ts
│   │   ├── workspaceService.ts
│   │   ├── monitoringService.ts
│   │   └── websocketService.ts
│   │
│   ├── stores/                      # 状态管理
│   │   ├── workflowStore.ts
│   │   ├── skillPackStore.ts
│   │   ├── rulePackStore.ts
│   │   ├── workspaceStore.ts
│   │   ├── userStore.ts
│   │   └── uiStore.ts
│   │
│   ├── types/                       # 类型定义
│   │   ├── workflow.ts
│   │   ├── skillPack.ts
│   │   ├── rulePack.ts
│   │   ├── workspace.ts
│   │   ├── monitoring.ts
│   │   └── api.ts
│   │
│   ├── utils/                       # 工具函数
│   │   ├── formatters.ts
│   │   ├── validators.ts
│   │   ├── converters.ts
│   │   └── constants.ts
│   │
│   └── assets/                      # 静态资源
│       ├── styles/
│       │   ├── global.css
│       │   ├── variables.css
│       │   └── themes/
│       │       ├── light.css
│       │       └── dark.css
│       │
│       └── images/
│           └── icons/
│
├── public/                          # 公共资源
├── tests/                           # 前端测试
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── package.json
├── tsconfig.json
├── vite.config.ts
├── vitest.config.ts
├── playwright.config.ts
└── .env.example
```

### 3. 核心页面设计

#### 3.1 仪表盘 (Dashboard)

```typescript
// pages/Dashboard/index.tsx

import React, { useEffect, useState } from 'react';
import { OverviewCard } from './OverviewCard';
import { ActivityFeed } from './ActivityFeed';
import { QuickActions } from './QuickActions';
import { RecentRuns } from './RecentRuns';
import { useWorkflowStore } from '../../stores/workflowStore';
import { useSkillPackStore } from '../../stores/skillPackStore';

interface DashboardData {
  totalWorkflows: number;
  activeWorkflows: number;
  completedWorkflows: number;
  failedWorkflows: number;
  totalSkillPacks: number;
  totalRulePacks: number;
  activeWorkspaces: number;
  recentRuns: WorkflowRun[];
  activityFeed: ActivityItem[];
}

export function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const { fetchWorkflowStats } = useWorkflowStore();
  const { fetchSkillPackStats } = useSkillPackStore();

  useEffect(() => {
    loadDashboardData();
  }, []);

  const loadDashboardData = async () => {
    try {
      const [workflowStats, skillPackStats] = await Promise.all([
        fetchWorkflowStats(),
        fetchSkillPackStats()
      ]);

      setData({
        totalWorkflows: workflowStats.total,
        activeWorkflows: workflowStats.active,
        completedWorkflows: workflowStats.completed,
        failedWorkflows: workflowStats.failed,
        totalSkillPacks: skillPackStats.total,
        totalRulePacks: skillPackStats.total,
        activeWorkspaces: skillPackStats.activeWorkspaces,
        recentRuns: workflowStats.recentRuns,
        activityFeed: workflowStats.activityFeed
      });
    } catch (error) {
      console.error('Failed to load dashboard data:', error);
    } finally {
      setLoading(false);
    }
  };

  if (loading) {
    return <DashboardSkeleton />;
  }

  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <h1>仪表盘</h1>
        <p>查看工作流执行情况、技能包和规则包的总体状态</p>
      </div>

      <div className="overview-grid">
        <OverviewCard
          title="总工作流"
          value={data.totalWorkflows}
          icon="workflow"
          trend={+12.5}
          trendLabel="vs 上周"
        />
        <OverviewCard
          title="活跃工作流"
          value={data.activeWorkflows}
          icon="active"
          status="active"
        />
        <OverviewCard
          title="完成工作流"
          value={data.completedWorkflows}
          icon="completed"
          status="success"
        />
        <OverviewCard
          title="失败工作流"
          value={data.failedWorkflows}
          icon="failed"
          status="error"
        />
        <OverviewCard
          title="技能包"
          value={data.totalSkillPacks}
          icon="skill-pack"
        />
        <OverviewCard
          title="规则包"
          value={data.totalRulePacks}
          icon="rule-pack"
        />
      </div>

      <div className="dashboard-content">
        <div className="recent-runs-section">
          <h2>最近运行</h2>
          <RecentRuns runs={data.recentRuns} />
        </div>

        <div className="activity-feed-section">
          <h2>活动流</h2>
          <ActivityFeed items={data.activityFeed} />
        </div>

        <div className="quick-actions-section">
          <h2>快速操作</h2>
          <QuickActions />
        </div>
      </div>
    </div>
  );
}
```

#### 3.2 工作流编辑器

```typescript
// pages/WorkflowEditor/index.tsx

import React, { useCallback, useState } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Connection,
  addEdge,
  Background,
  Controls,
  MiniMap,
  NodeTypes,
  useNodesState,
  useEdgesState,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { NodePalette } from './NodePalette';
import { PropertyPanel } from './PropertyPanel';
import { ValidationPanel } from './ValidationPanel';
import { CustomNode } from './CustomNode';

// 自定义节点类型
const nodeTypes: NodeTypes = {
  input: CustomNode,
  process: CustomNode,
  decision: CustomNode,
  output: CustomNode,
  skill: CustomNode,
  review: CustomNode,
};

// 初始节点
const initialNodes: Node[] = [
  {
    id: '1',
    type: 'input',
    position: { x: 100, y: 100 },
    data: {
      label: '需求输入',
      nodeType: 'input',
      config: {
        source: 'manual',
        inputType: 'text',
      }
    },
  },
  {
    id: '2',
    type: 'process',
    position: { x: 400, y: 100 },
    data: {
      label: '需求分析',
      nodeType: 'process',
      config: {
        capability: 'requirement.analyze',
        model: 'claude',
      }
    },
  },
];

const initialEdges: Edge[] = [
  { id: 'e1-2', source: '1', target: '2', animated: true },
];

export function WorkflowEditorPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [validationResult, setValidationResult] = useState<any>(null);

  const onConnect = useCallback(
    (connection: Connection) => setEdges((eds) => addEdge(connection, eds)),
    [setEdges]
  );

  const onNodeClick = useCallback((event: React.MouseEvent, node: Node) => {
    setSelectedNode(node);
    setSelectedEdge(null);
  }, []);

  const onEdgeClick = useCallback((event: React.MouseEvent, edge: Edge) => {
    setSelectedEdge(edge);
    setSelectedNode(null);
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      const nodeType = event.dataTransfer.getData('application/reactflow');
      if (!nodeType) return;

      const position = {
        x: event.clientX - 200,
        y: event.clientY - 100,
      };

      const newNode: Node = {
        id: `node_${Date.now()}`,
        type: nodeType,
        position,
        data: {
          label: getNodeLabel(nodeType),
          nodeType,
          config: {},
        },
      };

      setNodes((nds) => nds.concat(newNode));
    },
    [setNodes]
  );

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onSave = async () => {
    // 保存工作流
    const workflowData = {
      nodes: nodes.map(node => ({
        id: node.id,
        type: node.type,
        position: node.position,
        data: node.data,
      })),
      edges: edges.map(edge => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
      })),
    };
    // 调用保存API
    await saveWorkflow(workflowData);
  };

  const onValidate = () => {
    // 验证工作流
    const result = validateWorkflow(nodes, edges);
    setValidationResult(result);
  };

  return (
    <div className="workflow-editor">
      <div className="editor-header">
        <div className="editor-title">
          <h1>工作流编辑器</h1>
          <span className="workflow-name">未命名工作流</span>
        </div>
        <div className="editor-actions">
          <button onClick={onValidate}>验证</button>
          <button onClick={onSave}>保存</button>
          <button onClick={onRun}>运行</button>
        </div>
      </div>

      <div className="editor-body">
        <NodePalette onDragStart={onDragStart} />

        <div
          className="editor-canvas"
          onDrop={onDrop}
          onDragOver={onDragOver}
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            nodeTypes={nodeTypes}
            fitView
            attributionPosition="bottom-left"
          >
            <Background />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>

        {(selectedNode || selectedEdge) && (
          <PropertyPanel
            selectedNode={selectedNode}
            selectedEdge={selectedEdge}
            onUpdate={onUpdateNode}
          />
        )}
      </div>

      {validationResult && (
        <ValidationPanel result={validationResult} />
      )}
    </div>
  );
}
```

#### 3.3 技能包市场

```typescript
// pages/SkillPack/index.tsx

import React, { useState, useEffect } from 'react';
import { SkillPackList } from './SkillPackList';
import { SkillPackDetail } from './SkillPackDetail';
import { useSkillPackStore } from '../../stores/skillPackStore';

export function SkillPackPage() {
  const [view, setView] = useState<'list' | 'detail'>('list');
  const [selectedPack, setSelectedPack] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [sortBy, setSortBy] = useState('downloads');
  const { packs, loading, error, fetchPacks, installPack } = useSkillPackStore();

  useEffect(() => {
    fetchPacks({
      query: searchQuery,
      category: categoryFilter,
      sortBy,
    });
  }, [searchQuery, categoryFilter, sortBy]);

  const handleInstall = async (packId: string) => {
    await installPack(packId);
  };

  return (
    <div className="skill-pack-page">
      <div className="skill-pack-header">
        <h1>技能包市场</h1>
        <p>发现和安装技能包来扩展你的工作流能力</p>

        <div className="skill-pack-toolbar">
          <SearchInput
            value={searchQuery}
            onChange={setSearchQuery}
            placeholder="搜索技能包..."
          />
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
          >
            <option value="all">全部分类</option>
            <option value="frontend">前端开发</option>
            <option value="backend">后端开发</option>
            <option value="devops">DevOps</option>
            <option value="security">安全</option>
            <option value="testing">测试</option>
          </select>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
          >
            <option value="downloads">按下载量</option>
            <option value="rating">按评分</option>
            <option value="updated">按更新时间</option>
          </select>
        </div>
      </div>

      {loading ? (
        <SkillPackSkeleton />
      ) : error ? (
        <ErrorState message={error} />
      ) : (
        <SkillPackList
          packs={packs}
          onSelect={(packId) => {
            setSelectedPack(packId);
            setView('detail');
          }}
          onInstall={handleInstall}
        />
      )}

      {view === 'detail' && selectedPack && (
        <SkillPackDetail
          packId={selectedPack}
          onClose={() => setView('list')}
        />
      )}
    </div>
  );
}
```

### 4. 状态管理

```typescript
// stores/workflowStore.ts

import { create } from 'zustand';
import { workflowService } from '../services/workflowService';
import type { Workflow, WorkflowRun, WorkflowStats } from '../types/workflow';

interface WorkflowState {
  workflows: Workflow[];
  activeWorkflow: Workflow | null;
  runs: WorkflowRun[];
  stats: WorkflowStats | null;
  loading: boolean;
  error: string | null;

  // Actions
  fetchWorkflows: () => Promise<void>;
  fetchWorkflow: (id: string) => Promise<void>;
  createWorkflow: (data: any) => Promise<Workflow>;
  updateWorkflow: (id: string, data: any) => Promise<void>;
  deleteWorkflow: (id: string) => Promise<void>;
  runWorkflow: (id: string) => Promise<WorkflowRun>;
  fetchRuns: (workflowId: string) => Promise<void>;
  fetchStats: () => Promise<void>;
}

export const useWorkflowStore = create<WorkflowState>((set, get) => ({
  workflows: [],
  activeWorkflow: null,
  runs: [],
  stats: null,
  loading: false,
  error: null,

  fetchWorkflows: async () => {
    set({ loading: true, error: null });
    try {
      const workflows = await workflowService.list();
      set({ workflows, loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  fetchWorkflow: async (id: string) => {
    set({ loading: true, error: null });
    try {
      const workflow = await workflowService.get(id);
      set({ activeWorkflow: workflow, loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  createWorkflow: async (data: any) => {
    set({ loading: true, error: null });
    try {
      const workflow = await workflowService.create(data);
      set((state) => ({
        workflows: [...state.workflows, workflow],
        loading: false,
      }));
      return workflow;
    } catch (error) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  updateWorkflow: async (id: string, data: any) => {
    set({ loading: true, error: null });
    try {
      const workflow = await workflowService.update(id, data);
      set((state) => ({
        workflows: state.workflows.map(w => w.id === id ? workflow : w),
        activeWorkflow: workflow,
        loading: false,
      }));
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  deleteWorkflow: async (id: string) => {
    set({ loading: true, error: null });
    try {
      await workflowService.delete(id);
      set((state) => ({
        workflows: state.workflows.filter(w => w.id !== id),
        loading: false,
      }));
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  runWorkflow: async (id: string) => {
    set({ loading: true, error: null });
    try {
      const run = await workflowService.run(id);
      set((state) => ({
        runs: [...state.runs, run],
        loading: false,
      }));
      return run;
    } catch (error) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  fetchRuns: async (workflowId: string) => {
    set({ loading: true, error: null });
    try {
      const runs = await workflowService.listRuns(workflowId);
      set({ runs, loading: false });
    } catch (error) {
      set({ error: error.message, loading: false });
    }
  },

  fetchStats: async () => {
    try {
      const stats = await workflowService.getStats();
      set({ stats });
    } catch (error) {
      set({ error: error.message });
    }
  },
}));
```

### 5. 实时通信 (WebSocket)

```typescript
// services/websocketService.ts

import { EventEmitter } from 'events';

export class WebSocketService extends EventEmitter {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;

  constructor(private url: string) {
    super();
  }

  connect(): void {
    try {
      this.ws = new WebSocket(this.url);
      this.setupEventHandlers();
    } catch (error) {
      console.error('WebSocket connection failed:', error);
      this.scheduleReconnect();
    }
  }

  private setupEventHandlers(): void {
    if (!this.ws) return;

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      this.reconnectAttempts = 0;
      this.emit('connected');
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.handleMessage(data);
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error);
      }
    };

    this.ws.onclose = () => {
      console.log('WebSocket disconnected');
      this.emit('disconnected');
      this.scheduleReconnect();
    };

    this.ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      this.emit('error', error);
    };
  }

  private handleMessage(data: any): void {
    switch (data.type) {
      case 'workflow_status':
        this.emit('workflow_status', data.payload);
        break;
      case 'run_progress':
        this.emit('run_progress', data.payload);
        break;
      case 'run_completed':
        this.emit('run_completed', data.payload);
        break;
      case 'run_failed':
        this.emit('run_failed', data.payload);
        break;
      case 'log_message':
        this.emit('log_message', data.payload);
        break;
      case 'notification':
        this.emit('notification', data.payload);
        break;
      default:
        console.warn('Unknown WebSocket message type:', data.type);
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('Max reconnect attempts reached');
      this.emit('reconnect_failed');
      return;
    }

    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

    setTimeout(() => {
      console.log(`Reconnecting (attempt ${this.reconnectAttempts})...`);
      this.connect();
    }, delay);
  }

  subscribe(channel: string, callback: (data: any) => void): () => void {
    this.on(channel, callback);
    return () => {
      this.off(channel, callback);
    };
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}
```

### 6. 主题系统

```css
/* assets/styles/variables.css */

:root {
  /* 颜色系统 */
  --color-primary: #3b82f6;
  --color-primary-hover: #2563eb;
  --color-primary-light: #dbeafe;
  --color-secondary: #8b5cf6;
  --color-secondary-hover: #7c3aed;
  --color-success: #10b981;
  --color-success-light: #d1fae5;
  --color-warning: #f59e0b;
  --color-warning-light: #fef3c7;
  --color-error: #ef4444;
  --color-error-light: #fee2e2;
  --color-info: #0ea5e9;
  --color-info-light: #e0f2fe;

  /* 中性色 */
  --color-gray-50: #f9fafb;
  --color-gray-100: #f3f4f6;
  --color-gray-200: #e5e7eb;
  --color-gray-300: #d1d5db;
  --color-gray-400: #9ca3af;
  --color-gray-500: #6b7280;
  --color-gray-600: #4b5563;
  --color-gray-700: #374151;
  --color-gray-800: #1f2937;
  --color-gray-900: #111827;

  /* 间距系统 */
  --spacing-xs: 4px;
  --spacing-sm: 8px;
  --spacing-md: 16px;
  --spacing-lg: 24px;
  --spacing-xl: 32px;
  --spacing-2xl: 48px;
  --spacing-3xl: 64px;

  /* 字体系统 */
  --font-family-base: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-family-mono: 'JetBrains Mono', 'Fira Code', monospace;
  --font-size-xs: 12px;
  --font-size-sm: 14px;
  --font-size-md: 16px;
  --font-size-lg: 18px;
  --font-size-xl: 20px;
  --font-size-2xl: 24px;
  --font-size-3xl: 30px;

  /* 圆角 */
  --border-radius-sm: 4px;
  --border-radius-md: 8px;
  --border-radius-lg: 12px;
  --border-radius-xl: 16px;
  --border-radius-full: 9999px;

  /* 阴影 */
  --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1);

  /* 动画 */
  --transition-fast: 150ms;
  --transition-normal: 250ms;
  --transition-slow: 350ms;
  --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
}

/* 深色主题 */
[data-theme='dark'] {
  --color-primary: #60a5fa;
  --color-primary-hover: #3b82f6;
  --color-primary-light: #1e3a5f;
  --color-secondary: #a78bfa;
  --color-secondary-hover: #8b5cf6;
  --color-success: #34d399;
  --color-success-light: #064e3b;
  --color-warning: #fbbf24;
  --color-warning-light: #78350f;
  --color-error: #f87171;
  --color-error-light: #7f1d1d;
  --color-info: #38bdf8;
  --color-info-light: #0c4a6e;

  --color-gray-50: #111827;
  --color-gray-100: #1f2937;
  --color-gray-200: #374151;
  --color-gray-300: #4b5563;
  --color-gray-400: #6b7280;
  --color-gray-500: #9ca3af;
  --color-gray-600: #d1d5db;
  --color-gray-700: #e5e7eb;
  --color-gray-800: #f3f4f6;
  --color-gray-900: #f9fafb;

  --shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.3);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.4);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.4);
}
```

### 7. 后端 API 扩展

```python
# app/server.py (扩展部分)

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from app.services.workflow_service import WorkflowService
from app.services.skill_pack_service import SkillPackService
from app.services.rule_pack_service import RulePackService
from app.services.workspace_service import WorkspaceService

app = Flask(__name__, static_folder='../ui/dist', static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

# 初始化服务
workflow_service = WorkflowService()
skill_pack_service = SkillPackService()
rule_pack_service = RulePackService()
workspace_service = WorkspaceService()

# ============ 工作流 API ============

@app.route('/api/workflows', methods=['GET'])
def list_workflows():
    """列出所有工作流"""
    workflows = workflow_service.list_all()
    return jsonify(workflows)

@app.route('/api/workflows', methods=['POST'])
def create_workflow():
    """创建工作流"""
    data = request.get_json()
    workflow = workflow_service.create(data)
    return jsonify(workflow), 201

@app.route('/api/workflows/<workflow_id>', methods=['GET'])
def get_workflow(workflow_id):
    """获取工作流详情"""
    workflow = workflow_service.get(workflow_id)
    if not workflow:
        return jsonify({"error": "Workflow not found"}), 404
    return jsonify(workflow)

@app.route('/api/workflows/<workflow_id>', methods=['PUT'])
def update_workflow(workflow_id):
    """更新工作流"""
    data = request.get_json()
    workflow = workflow_service.update(workflow_id, data)
    return jsonify(workflow)

@app.route('/api/workflows/<workflow_id>', methods=['DELETE'])
def delete_workflow(workflow_id):
    """删除工作流"""
    workflow_service.delete(workflow_id)
    return '', 204

@app.route('/api/workflows/<workflow_id>/run', methods=['POST'])
def run_workflow(workflow_id):
    """运行工作流"""
    run = workflow_service.run(workflow_id)
    return jsonify(run), 202

@app.route('/api/workflows/<workflow_id>/runs', methods=['GET'])
def list_workflow_runs(workflow_id):
    """列出工作流运行记录"""
    runs = workflow_service.list_runs(workflow_id)
    return jsonify(runs)

# ============ 技能包 API ============

@app.route('/api/skill-packs', methods=['GET'])
def list_skill_packs():
    """列出技能包"""
    packs = skill_pack_service.list_all()
    return jsonify(packs)

@app.route('/api/skill-packs/<pack_id>', methods=['GET'])
def get_skill_pack(pack_id):
    """获取技能包详情"""
    pack = skill_pack_service.get(pack_id)
    if not pack:
        return jsonify({"error": "Skill pack not found"}), 404
    return jsonify(pack)

@app.route('/api/skill-packs/<pack_id>/install', methods=['POST'])
def install_skill_pack(pack_id):
    """安装技能包"""
    result = skill_pack_service.install(pack_id)
    return jsonify(result)

@app.route('/api/skill-packs/<pack_id>/uninstall', methods=['DELETE'])
def uninstall_skill_pack(pack_id):
    """卸载技能包"""
    result = skill_pack_service.uninstall(pack_id)
    return jsonify(result)

# ============ 规则包 API ============

@app.route('/api/rule-packs', methods=['GET'])
def list_rule_packs():
    """列出规则包"""
    packs = rule_pack_service.list_all()
    return jsonify(packs)

@app.route('/api/rule-packs/<pack_id>', methods=['GET'])
def get_rule_pack(pack_id):
    """获取规则包详情"""
    pack = rule_pack_service.get(pack_id)
    if not pack:
        return jsonify({"error": "Rule pack not found"}), 404
    return jsonify(pack)

@app.route('/api/rule-packs/validate', methods=['POST'])
def validate_rule_pack():
    """验证规则包"""
    data = request.get_json()
    result = rule_pack_service.validate(data)
    return jsonify(result)

# ============ 工作空间 API ============

@app.route('/api/workspaces', methods=['GET'])
def list_workspaces():
    """列出工作空间"""
    workspaces = workspace_service.list_all()
    return jsonify(workspaces)

@app.route('/api/workspaces', methods=['POST'])
def create_workspace():
    """创建工作空间"""
    data = request.get_json()
    workspace = workspace_service.create(data)
    return jsonify(workspace), 201

@app.route('/api/workspaces/<workspace_id>', methods=['GET'])
def get_workspace(workspace_id):
    """获取工作空间详情"""
    workspace = workspace_service.get(workspace_id)
    if not workspace:
        return jsonify({"error": "Workspace not found"}), 404
    return jsonify(workspace)

# ============ 监控 API ============

@app.route('/api/monitoring/stats', methods=['GET'])
def get_monitoring_stats():
    """获取监控统计"""
    stats = {
        "total_runs": workflow_service.get_total_runs(),
        "success_rate": workflow_service.get_success_rate(),
        "average_duration": workflow_service.get_average_duration(),
        "error_rate": workflow_service.get_error_rate(),
    }
    return jsonify(stats)

# ============ WebSocket 事件 ============

@socketio.on('connect')
def handle_connect():
    """处理连接"""
    emit('connected', {'message': 'Connected to Harness server'})

@socketio.on('subscribe_workflow')
def handle_subscribe_workflow(data):
    """订阅工作流"""
    workflow_id = data.get('workflow_id')
    if workflow_id:
        # 加入工作流房间
        from flask_socketio import join_room
        join_room(f'workflow_{workflow_id}')

@socketio.on('disconnect')
def handle_disconnect():
    """处理断开连接"""
    print('Client disconnected')

# ============ 静态文件服务 ============

@app.route('/')
def serve_index():
    """服务前端首页"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    """服务静态文件"""
    if path.startswith('api/'):
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(app.static_folder, path)
```

### 8. 响应式设计规范

| 断点 | 宽度 | 布局 |
|------|------|------|
| 移动端 | < 640px | 单列 |
| 平板 | 640px - 1024px | 双列 |
| 桌面 | > 1024px | 多列 |

```css
/* 响应式布局 */
@media (max-width: 640px) {
  .overview-grid {
    grid-template-columns: 1fr;
  }
  .dashboard-content {
    flex-direction: column;
  }
}

@media (min-width: 640px) and (max-width: 1024px) {
  .overview-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .dashboard-content {
    flex-wrap: wrap;
  }
}

@media (min-width: 1024px) {
  .overview-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .dashboard-content {
    flex-direction: row;
  }
}
```

### 9. 测试策略

#### 9.1 单元测试

```typescript
// tests/unit/components/OverviewCard.test.tsx

import { render, screen } from '@testing-library/react';
import { OverviewCard } from '../../../src/components/pages/Dashboard/OverviewCard';

describe('OverviewCard', () => {
  it('renders title and value', () => {
    render(
      <OverviewCard
        title="总工作流"
        value={42}
        icon="workflow"
      />
    );

    expect(screen.getByText('总工作流')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('shows trend when provided', () => {
    render(
      <OverviewCard
        title="总工作流"
        value={42}
        icon="workflow"
        trend={+12.5}
        trendLabel="vs 上周"
      />
    );

    expect(screen.getByText('+12.5%')).toBeInTheDocument();
    expect(screen.getByText('vs 上周')).toBeInTheDocument();
  });
});
```

#### 9.2 E2E 测试

```typescript
// tests/e2e/workflow-editor.spec.ts

import { test, expect } from '@playwright/test';

test.describe('工作流编辑器', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('http://localhost:5173/workflows/editor');
  });

  test('创建新工作流', async ({ page }) => {
    // 拖拽节点到画布
    await page.dragAndDrop(
      '[data-testid="node-input"]',
      '[data-testid="editor-canvas"]'
    );

    // 验证节点已添加
    expect(page.locator('[data-testid="canvas-node"]')).toHaveCount(1);

    // 配置节点
    await page.click('[data-testid="canvas-node"]');
    await page.fill('[data-testid="node-label"]', '需求输入');

    // 保存工作流
    await page.click('[data-testid="save-button"]');
    await expect(page.locator('[data-testid="save-success"]')).toBeVisible();
  });

  test('验证工作流', async ({ page }) => {
    // 添加节点和边
    // ... 拖拽操作

    // 点击验证
    await page.click('[data-testid="validate-button"]');

    // 检查验证结果
    await expect(page.locator('[data-testid="validation-result"]')).toBeVisible();
    await expect(page.locator('[data-testid="validation-success"]')).toBeVisible();
  });
});
```

### 10. 性能优化

#### 10.1 代码分割

```typescript
// router.tsx

import { lazy, Suspense } from 'react';
import { createBrowserRouter } from 'react-router-dom';

// 懒加载页面
const DashboardPage = lazy(() => import('./pages/Dashboard'));
const WorkflowEditorPage = lazy(() => import('./pages/WorkflowEditor'));
const SkillPackPage = lazy(() => import('./pages/SkillPack'));
const RulePackPage = lazy(() => import('./pages/RulePack'));
const WorkspacePage = lazy(() => import('./pages/Workspace'));
const MonitoringPage = lazy(() => import('./pages/Monitoring'));

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
    children: [
      {
        path: '/',
        element: (
          <Suspense fallback={<Loading />}>
            <DashboardPage />
          </Suspense>
        ),
      },
      {
        path: '/workflows/editor',
        element: (
          <Suspense fallback={<Loading />}>
            <WorkflowEditorPage />
          </Suspense>
        ),
      },
      // ... 其他路由
    ],
  },
]);
```

#### 10.2 虚拟化列表

```typescript
// components/common/VirtualList.tsx

import { useVirtualizer } from '@tanstack/react-virtual';
import { useRef } from 'react';

interface VirtualListProps<T> {
  items: T[];
  renderItem: (item: T, index: number) => React.ReactNode;
  itemHeight: number;
}

export function VirtualList<T>({ items, renderItem, itemHeight }: VirtualListProps<T>) {
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => itemHeight,
    overscan: 5,
  });

  return (
    <div
      ref={parentRef}
      style={{ height: '600px', overflow: 'auto' }}
    >
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((virtualItem) => (
          <div
            key={virtualItem.key}
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              width: '100%',
              height: `${virtualItem.size}px`,
              transform: `translateY(${virtualItem.start}px)`,
            }}
          >
            {renderItem(items[virtualItem.index], virtualItem.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
```

### 11. 交付物清单

| 交付物 | 描述 | 状态 |
|--------|------|------|
| 前端项目脚手架 | package.json, tsconfig.json, vite.config.ts | ⬜ |
| 布局组件 | AppLayout, Sidebar, Header | ⬜ |
| 通用组件 | Button, Modal, Card, Table 等 | ⬜ |
| 仪表盘页面 | Dashboard | ⬜ |
| 工作流编辑器 | WorkflowEditor | ⬜ |
| 技能包市场 | SkillPack | ⬜ |
| 规则包管理 | RulePack | ⬜ |
| 工作空间管理 | Workspace | ⬜ |
| 监控中心 | Monitoring | ⬜ |
| 状态管理 | Zustand stores | ⬜ |
| API 服务层 | services/*.ts | ⬜ |
| WebSocket 通信 | websocketService.ts | ⬜ |
| 主题系统 | light.css, dark.css | ⬜ |
| 后端 API 扩展 | server.py | ⬜ |
| 单元测试 | tests/unit | ⬜ |
| E2E 测试 | tests/e2e | ⬜ |
| 架构文档 | 本文档 | ✅ |

---

**下一步行动**：进入阶段4的代码实现阶段