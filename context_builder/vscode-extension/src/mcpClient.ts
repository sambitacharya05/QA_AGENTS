import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

export class MCPClient {
    private process: ChildProcess | null = null;
    private requestId = 1;
    private pendingRequests = new Map<number, { resolve: (val: any) => void; reject: (err: any) => void }>();
    private buffer = '';
    private readyPromise: Promise<void> | null = null;
    private readyResolve: (() => void) | null = null;
    private readyReject: ((err: Error) => void) | null = null;
    private serverCwd: string = '';

    constructor(
        private workspaceRoot: string,
        // Lambda instead of a plain string so every start() call (including the
        // auto-restart inside callTool()) reads the live value from disk rather
        // than the value that may have been empty when the extension first activated.
        private getServerDirectory: () => string | undefined,
    ) {}

    /**
     * Spawns the Python MCP Server process and performs the required MCP initialize handshake.
     *
     * Server path resolution priority:
     *   1. `serverDirectory` setting (contextBuilder.serverDirectory) if provided
     *   2. Workspace root (existing behavior — workspace must contain main.py)
     *   3. context_builder_release* subfolder inside workspace root (legacy fallback)
     */
    public start(): void {
        // ── Server path resolution ──────────────────────────────────────────────
        // Priority 1: Use the configured serverDirectory if provided.
        // Priority 2: Look in workspace root for main.py.
        // Priority 3: Search workspace root for a context_builder_release* subfolder.
        let serverPath: string;
        let serverCwd: string;

        // Call the lambda each time start() runs so we always get the current
        // on-disk value — even if the extension activated before settings.json existed.
        const serverDirectory = this.getServerDirectory();

        if (serverDirectory) {
            // Explicit server directory set via VS Code setting
            serverPath = path.join(serverDirectory, 'main.py');
            serverCwd = serverDirectory;

            if (!fs.existsSync(serverPath)) {
                console.error(
                    `[MCP Client] contextBuilder.serverDirectory is set to "${serverDirectory}" ` +
                    `but main.py was not found there. Falling back to workspace root.`
                );
                serverPath = path.join(this.workspaceRoot, 'main.py');
                serverCwd = this.workspaceRoot;
            }
        } else {
            // Default: look in workspace root
            serverPath = path.join(this.workspaceRoot, 'main.py');
            serverCwd = this.workspaceRoot;

            if (!fs.existsSync(serverPath)) {
                // Legacy release directory search
                try {
                    const dirs = fs.readdirSync(this.workspaceRoot, { withFileTypes: true });
                    const releaseDir = dirs.find(
                        (d: any) => d.isDirectory() && d.name.startsWith('context_builder_release')
                    );
                    if (releaseDir) {
                        serverPath = path.join(this.workspaceRoot, releaseDir.name, 'main.py');
                        serverCwd = path.join(this.workspaceRoot, releaseDir.name);
                    }
                } catch (e) {
                    console.error('[MCP Client] Failed to search for release directory:', e);
                }
            }
        }

        this.serverCwd = serverCwd;

        // ── Python binary resolution (cross-platform) ───────────────────────────
        // Prefer the virtualenv Python in serverCwd over system Python.
        let pythonCmd = 'python3';  // safe system default

        if (process.platform === 'win32') {
            const winVenv = path.join(serverCwd, '.venv', 'Scripts', 'python.exe');
            if (fs.existsSync(winVenv)) {
                pythonCmd = winVenv;
            }
        } else {
            const py3 = path.join(serverCwd, '.venv', 'bin', 'python3');
            const py  = path.join(serverCwd, '.venv', 'bin', 'python');
            if (fs.existsSync(py3)) {
                pythonCmd = py3;
            } else if (fs.existsSync(py)) {
                pythonCmd = py;
            }
        }

        console.log(`[MCP Client] Starting context_builder server: ${pythonCmd} ${serverPath} (cwd: ${serverCwd})`);

        // Create the readyPromise before spawning so callTool() can await it
        this.readyPromise = new Promise<void>((resolve, reject) => {
            this.readyResolve = resolve;
            this.readyReject = reject;
        });

        // ── Process spawn ───────────────────────────────────────────────────────
        this.process = spawn(pythonCmd, [serverPath], {
            cwd: serverCwd,
            env: { ...process.env }
        });

        this.process.stdout?.on('data', (data) => {
            this.buffer += data.toString();
            this.processBuffer();
        });

        this.process.stderr?.on('data', (data) => {
            console.error(`[MCP Server Error] ${data.toString()}`);
        });

        this.process.on('close', (code) => {
            console.log(`[MCP Server] Process exited with code ${code}`);
            this.process = null;

            // If the process died before the handshake completed, reject readyPromise
            // immediately so callTool() fails fast instead of waiting the full 60 s timeout.
            if (this.readyReject) {
                this.readyReject(new Error(
                    `MCP Python server exited (code ${code}) before completing the initialize handshake. ` +
                    `Check the Output panel (@build_context) for [MCP Server Error] lines — ` +
                    `the server likely failed to start (missing main.py, bad venv, or import error).`
                ));
                this.readyReject = null;
                this.readyResolve = null;
            }

            // Reject any in-flight tool requests
            for (const [id, { reject }] of this.pendingRequests) {
                reject(new Error(`MCP Server process exited with code ${code}`));
            }
            this.pendingRequests.clear();
        });

        // Initiate the MCP protocol handshake
        this.sendInitialize();
    }

    /**
     * Sends the MCP initialize request (Step 1 of the 3-step handshake).
     */
    private sendInitialize(): void {
        const initRequest = {
            jsonrpc: '2.0',
            id: 0,
            method: 'initialize',
            params: {
                protocolVersion: '2024-11-05',
                capabilities: {},
                clientInfo: {
                    name: 'context-builder-vscode',
                    version: '1.1.0'
                }
            }
        };
        console.log('[MCP Client] Sending initialize handshake...');
        this.process?.stdin?.write(JSON.stringify(initRequest) + '\n');
    }

    /**
     * Sends the initialized notification (Step 3 of the 3-step handshake).
     */
    private sendInitialized(): void {
        const notification = {
            jsonrpc: '2.0',
            method: 'notifications/initialized'
        };
        this.process?.stdin?.write(JSON.stringify(notification) + '\n');
        console.log('[MCP Client] Handshake complete. Server is ready.');

        // Resolve the readyPromise so queued callTool() invocations can proceed
        if (this.readyResolve) {
            this.readyResolve();
            this.readyResolve = null;
            this.readyReject = null;   // handshake succeeded — no longer need the reject path
        }
    }

    /**
     * Shuts down the Python process.
     */
    public stop(): void {
        if (this.process) {
            this.process.kill();
            this.process = null;
        }
    }

    /**
     * Invokes a specific MCP tool using standard JSON-RPC protocol format.
     * Waits for the initialize handshake to complete before sending.
     */
    public async callTool(toolName: string, args: Record<string, any> = {}): Promise<any> {
        if (!this.process) {
            this.start();
        }

        // Wait for MCP handshake to complete before sending any tool calls.
        // Guard with a 60 s timeout — if the Python server never sends its
        // initialize response (crash, missing venv, stdout/stderr routing bug),
        // callTool() would hang here indefinitely because the 30 s tool-call
        // timeout below is only armed AFTER this await resolves.
        if (this.readyPromise) {
            const handshakeTimeout = new Promise<never>((_, reject) =>
                setTimeout(() => reject(new Error(
                    'MCP server did not complete the initialize handshake within 60 s. ' +
                    'Check the Output panel for [MCP Server Error] messages.'
                )), 60000)
            );
            await Promise.race([this.readyPromise, handshakeTimeout]);
        }

        return new Promise((resolve, reject) => {
            const id = this.requestId++;

            // Timeout after 30 seconds to prevent infinite hangs
            const timeout = setTimeout(() => {
                this.pendingRequests.delete(id);
                reject(new Error(`MCP tool call '${toolName}' timed out after 30s`));
            }, 30000);

            this.pendingRequests.set(id, {
                resolve: (val) => { clearTimeout(timeout); resolve(val); },
                reject: (err) => { clearTimeout(timeout); reject(err); }
            });

            const jsonRpcRequest = {
                jsonrpc: '2.0',
                method: 'tools/call',
                params: {
                    name: toolName,
                    arguments: args
                },
                id: id
            };

            this.process?.stdin?.write(JSON.stringify(jsonRpcRequest) + '\n');
        });
    }

    /**
     * Processes chunked stdio buffers, parsing complete JSON lines.
     * Handles both the initialize handshake response and regular tool call responses.
     */
    private processBuffer(): void {
        let newlineIndex;
        while ((newlineIndex = this.buffer.indexOf('\n')) !== -1) {
            const line = this.buffer.substring(0, newlineIndex).trim();
            this.buffer = this.buffer.substring(newlineIndex + 1);

            if (!line) {
                continue;
            }

            try {
                const response = JSON.parse(line);

                // Handle initialize handshake response (Step 2: server responds to id=0)
                if (response.id === 0 && response.result) {
                    console.log('[MCP Client] Received initialize response:', JSON.stringify(response.result.serverInfo || {}));
                    this.sendInitialized();
                    continue;
                }

                if (response.id && this.pendingRequests.has(response.id)) {
                    const { resolve, reject } = this.pendingRequests.get(response.id)!;
                    this.pendingRequests.delete(response.id);

                    if (response.error) {
                        reject(new Error(response.error.message || 'JSON-RPC invocation failed.'));
                    } else {
                        // Extract content text from MCP response format
                        const content = response.result?.content?.[0]?.text || response.result;
                        resolve(content);
                    }
                }
            } catch (err) {
                // Ignore incomplete lines or raw logs
            }
        }
    }
}
