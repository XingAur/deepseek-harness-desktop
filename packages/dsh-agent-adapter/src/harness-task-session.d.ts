import { type HarnessAgentRequest, type HarnessProcessTransportOptions, type HarnessTaskResult, type HarnessTaskStartPayload, type HarnessTransport } from './harness-bridge.js';
import { type HarnessAgentExecution, type HarnessAgentExecutionContext, type HarnessAgentExecutor, type HarnessEventSink } from './harness-host-handler.js';
export interface HarnessTaskSessionOptions {
    /** Injected in tests or when the host already owns the sidecar transport. */
    transport?: HarnessTransport;
    /** Used by desktop/CLI hosts when they want the adapter to spawn the sidecar. */
    sidecar?: HarnessProcessTransportOptions;
    execute: HarnessAgentExecutor;
    taskTimeoutMs?: number;
}
export interface HarnessTaskSession {
    start(payload: HarnessTaskStartPayload, requestId?: string): Promise<HarnessTaskResult>;
    cancel(requestId: string): void | Promise<void>;
    onEvent(listener: HarnessEventSink): () => void;
    dispose(): void;
}
/**
 * Bind one Harness decision session to one host model executor.
 *
 * The host may execute a request and report facts, but it has no replan API.
 * Replanning is therefore only possible when the sidecar sends another
 * execute-only request after it has evaluated the previous result.
 */
export declare function createHarnessTaskSession(options: HarnessTaskSessionOptions): HarnessTaskSession;
export type { HarnessAgentRequest, HarnessAgentExecution, HarnessAgentExecutionContext };
