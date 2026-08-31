import { type HarnessAgentRequest, type HarnessAgentResult } from './harness-bridge.js';
export interface HarnessAgentExecutionContext {
    readonly signal: AbortSignal;
    /** Only reports execution facts; it cannot alter the Harness decision. */
    emit(payload: Record<string, unknown>): void;
}
export interface HarnessAgentExecution {
    finalResponse?: Record<string, unknown>;
    exitCode?: number | null;
    errorCode?: string;
    finalResponseValidated?: boolean;
}
export type HarnessAgentExecutor = (request: HarnessAgentRequest, context: HarnessAgentExecutionContext) => Promise<HarnessAgentExecution>;
export type HarnessEventSink = (payload: Record<string, unknown>) => void;
export interface HarnessHostHandlerOptions {
    execute: HarnessAgentExecutor;
}
/**
 * Host-side execution boundary for a Harness request.
 *
 * The callback receives the exact prompt decided by Harness.  There is no
 * plan/replan callback here by design: a model may execute and report facts,
 * while only Harness may issue the next decision.
 */
export declare function createHarnessHostHandler(options: HarnessHostHandlerOptions): (request: HarnessAgentRequest, onEvent?: HarnessEventSink) => Promise<HarnessAgentResult>;
export declare function validateHarnessAgentRequest(value: unknown): HarnessAgentRequest;
