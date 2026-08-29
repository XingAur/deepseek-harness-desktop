import { type ChildProcessWithoutNullStreams, type SpawnOptions } from 'node:child_process';
export declare const HARNESS_HOST_SESSION_SCHEMA: "harness-host-session.v1";
export declare const HARNESS_BRIDGE_MAX_BYTES: number;
export type HarnessHostMessageType = 'agent.request' | 'agent.result' | 'session.event' | 'task.start' | 'task.result' | 'session.cancel';
export interface HarnessHostMessage {
    schema_version: typeof HARNESS_HOST_SESSION_SCHEMA;
    type: HarnessHostMessageType;
    request_id: string;
    payload: Record<string, unknown>;
}
export interface HarnessAgentRequest {
    schema_version: 'his-agent-backend-request.v1';
    role: 'worker' | 'reviewer';
    worktree_path: string;
    prompt: string;
    timeout_seconds: number;
    output_contract: {
        name: string;
        schema_version: string;
    };
    capabilities: string[];
}
export interface HarnessAgentResult {
    schema_version: 'his-agent-backend-result.v1';
    exit_code: number | null;
    error_code: string;
    event_count: number;
    final_response_sha256: string;
    canonical_final_response_sha256: string;
    final_response_validated: boolean;
    final_response?: Record<string, unknown>;
}
export interface HarnessTransport {
    send(message: HarnessHostMessage): void | Promise<void>;
    onMessage(listener: (message: unknown) => void): () => void;
    close?(): void | Promise<void>;
}
export interface HarnessTaskStartPayload {
    schema_version: 'harness-external-task.v1';
    task_contract_path: string;
    understanding_path: string;
    worktree_root: string;
    knowledge_home: string;
    authorization_id: string;
    agent_backend?: string;
}
export interface HarnessTaskResult {
    status: 'accepted' | 'completed' | 'blocked' | 'failed';
    error_code: string;
    understanding_sha256?: string;
    snapshot?: Record<string, unknown>;
}
export type HarnessAgentRequestListener = (request: HarnessAgentRequest, requestId: string) => void | Promise<void>;
export interface HarnessProcessTransportOptions {
    command: string;
    args?: readonly string[];
    cwd: string;
    env?: NodeJS.ProcessEnv;
    spawn?: (command: string, args: readonly string[], options: SpawnOptions) => ChildProcessWithoutNullStreams;
}
/** Spawn the provider-neutral Python sidecar with fixed args and a redacted env. */
export declare function createHarnessProcessTransport(options: HarnessProcessTransportOptions): HarnessTransport & {
    readonly pid: number | undefined;
};
export declare class HarnessBridgeClient {
    private readonly transport;
    private readonly pending;
    private readonly pendingTasks;
    private readonly eventListeners;
    private readonly agentRequestListeners;
    private readonly disposeTransport;
    private disposed;
    constructor(transport: HarnessTransport);
    awaitAgentResult(requestId: string, timeoutMs?: number): Promise<HarnessAgentResult>;
    awaitTaskResult(requestId: string, timeoutMs?: number): Promise<HarnessTaskResult>;
    sendAgentResult(requestId: string, result: HarnessAgentResult): void | Promise<void>;
    startTask(payload: HarnessTaskStartPayload, requestId?: string): string;
    cancelTask(requestId: string): void | Promise<void>;
    onEvent(listener: (payload: Record<string, unknown>) => void): () => void;
    onAgentRequest(listener: HarnessAgentRequestListener): () => void;
    sendEvent(requestId: string, payload: Record<string, unknown>): void | Promise<void>;
    dispose(): void;
    private handleMessage;
    private rejectPending;
}
export declare function validateHostMessage(value: unknown): HarnessHostMessage;
