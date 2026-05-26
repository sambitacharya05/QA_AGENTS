import { spawn, ChildProcess } from 'child_process';
import * as path from 'path';
import * as fs from 'fs';

export class McpClient {
  private process: ChildProcess | null = null;
  private requestId = 1;
  private pendingRequests = new Map<number, { resolve: (val: any) => void; reject: (err: any) => void }>();
  private buffer = '';
  private readyPromise: Promise<void> | null = null;
  private readyResolve: (() => void) | null = null;
  private readyReject: ((err: Error) => void) | null = null;

  /**
   * Guards against concurrent start() calls.
   * When true a second start() call returns the existing readyPromise instead
   * of spawning a second Python sub-process.
   */
  private starting = false;

  /**
   * @param workspaceRoot      Absolute path to the VS Code workspace root.
   * @param getTimeoutMs       Optional factory that returns the per-call timeout in
   *                           milliseconds.  Called fresh on every callTool()
   *                           invocation so that live VS Code config changes take
   *                           effect without reloading the extension.
   *                           Defaults to a constant 30 000 ms.
   * @param getEngineDirectory Optional factory that returns the absolute path to the
   *                           Test_case_creator installation directory (the folder
   *                           containing the engine/ module and .venv/).  Called at
   *                           start() time so that config changes take effect on the
   *                           next server (re)start without extension reload.
   *                           Returns '' by default, falling back to workspaceRoot.
   */
  constructor(
    private readonly workspaceRoot: string,
    private readonly getTimeoutMs: () => number = () => 30_000,
    private readonly getEngineDirectory: () => string = () => '',   // NEW
  ) {}

  /**
   * Spawns the Python MCP Server process and performs the required MCP
   * initialize handshake.
   *
   * Safe to call while a start is already in progress: the second caller
   * simply awaits the same readyPromise rather than spawning a new process.
   * On reconnect after a crash the stale buffer is cleared before the new
   * process is spawned to prevent residual data corruption in the JSON-RPC
   * parser.
   */
  public async start(): Promise<void> {
    // Concurrent-start guard: return the in-flight promise to the second caller.
    if (this.starting && this.readyPromise) {
      return this.readyPromise;
    }
    this.starting = true;

    // Buffer reset on reconnect: discard any partial JSON line left behind by
    // a previously crashed process so it cannot corrupt the first response
    // from the new process.
    this.buffer = '';

    // 1. Engine directory resolution ────────────────────────────────────────────
    // The engine/ module must be importable from the cwd. Use the configured
    // engineDirectory if set; otherwise fall back to the workspace root.
    // Python's -m mode adds cwd to sys.path[0], making 'engine' importable.
    const rawEngineDir = this.getEngineDirectory().trim();
    const engineDir = rawEngineDir || this.workspaceRoot;

    if (rawEngineDir && !fs.existsSync(engineDir)) {
      console.error(
        `[MCP Client] testCaseCreator.engineDirectory is set to "${engineDir}" ` +
        `but the directory does not exist. Falling back to workspace root.`
      );
    }

    // 2. Python binary resolution (cross-platform) ──────────────────────────────
    // Detection order: venv python3 → venv python → system python3 → system python
    let pythonCommand = 'python3';

    if (process.platform === 'win32') {
      const winVenv = path.join(engineDir, '.venv', 'Scripts', 'python.exe');
      if (fs.existsSync(winVenv)) {
        pythonCommand = winVenv;
      } else {
        pythonCommand = 'python';  // Windows system fallback
      }
    } else {
      const py3 = path.join(engineDir, '.venv', 'bin', 'python3');
      const py  = path.join(engineDir, '.venv', 'bin', 'python');
      if (fs.existsSync(py3)) {
        pythonCommand = py3;
      } else if (fs.existsSync(py)) {
        pythonCommand = py;
      }
    }

    // 3. Initialize the readyPromise so calls wait until handshake finishes
    this.readyPromise = new Promise<void>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });

    console.log(`[MCP Client] Spawning Python server via: ${pythonCommand} -m engine.mcp_server (cwd: ${engineDir})`);

    // 4. Spawn Python process running engine.mcp_server on stdio.
    // '-m engine.mcp_server' requires engineDir to be on sys.path.
    this.process = spawn(pythonCommand, ['-m', 'engine.mcp_server'], {
      cwd: engineDir,
      env: { ...process.env }
    });

    this.process.stdout?.on('data', (data) => {
      this.buffer += data.toString();
      this.processBuffer();
    });

    this.process.stderr?.on('data', (data) => {
      console.error(`[MCP Server Error] ${data.toString()}`);
    });

    this.process.on('error', (err) => {
      console.error("[MCP Client] Failed to spawn engine.mcp_server process:", err);
    });

    this.process.on('close', (code) => {
      console.log(`[MCP Client] engine.mcp_server process exited with code ${code}`);
      this.process = null;
      // Allow a future start() to spawn a fresh process
      this.starting = false;

      // If the process died before the handshake completed, reject readyPromise
      // immediately so callTool() fails fast instead of hanging indefinitely.
      if (this.readyReject) {
        this.readyReject(new Error(
          `MCP Python server exited (code ${code}) before completing the initialize handshake. ` +
          `Check the Output panel for [MCP Server Error] lines — ` +
          `the server likely failed to start (missing engine module, bad venv, or import error).`
        ));
        this.readyReject = null;
        this.readyResolve = null;
      }

      // Reject any in-flight tool requests
      for (const [, { reject }] of this.pendingRequests) {
        reject(new Error(`MCP Server process exited with code ${code}`));
      }
      this.pendingRequests.clear();
    });

    // 4. Initiate the MCP protocol handshake
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
          name: 'test-case-creator-vscode',
          version: '1.0.0'
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
      this.readyReject = null;  // handshake succeeded — no longer need the reject path
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
    this.starting = false;
  }

  /**
   * Invokes a specific MCP tool using standard JSON-RPC protocol format.
   * Waits for the initialize handshake to complete before sending.
   *
   * The per-call timeout is sourced from the ``getTimeoutMs`` factory
   * supplied at construction time, so VS Code configuration changes take
   * effect on the next call without reloading the extension.
   */
  public async callTool(toolName: string, args: Record<string, any> = {}): Promise<any> {
    if (!this.process) {
      await this.start();
    }

    // Wait for MCP handshake to complete before sending any tool calls.
    // Guard with a 60 s timeout — if the Python server never sends its
    // initialize response (crash, missing venv, import error), callTool()
    // would hang indefinitely because the per-call timeout below is only
    // armed AFTER this await resolves.
    if (this.readyPromise) {
      const handshakeTimeout = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error(
          'MCP server did not complete the initialize handshake within 60 s. ' +
          'Check the Output panel for [MCP Server Error] messages.'
        )), 60_000)
      );
      await Promise.race([this.readyPromise, handshakeTimeout]);
    }

    return new Promise((resolve, reject) => {
      const id = this.requestId++;
      const timeoutMs = this.getTimeoutMs();

      // Configurable timeout to prevent infinite hangs on large graphs or
      // slow environments.  The timeout value is read fresh each call so
      // that VS Code setting changes take effect without extension reload.
      const timeout = setTimeout(() => {
        this.pendingRequests.delete(id);
        reject(new Error(
          `MCP tool call '${toolName}' timed out after ${timeoutMs / 1000}s. ` +
          `Increase 'testCaseCreator.mcpTimeoutSeconds' in VS Code settings if your graph is large.`
        ));
      }, timeoutMs);

      this.pendingRequests.set(id, {
        resolve: (val) => { clearTimeout(timeout); resolve(val); },
        reject:  (err) => { clearTimeout(timeout); reject(err); }
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

            // FastMCP server returns results nested inside content array
            if (response.result && response.result.content && response.result.content[0]) {
              const textContent = response.result.content[0].text;
              try {
                // If it is a stringified JSON (from Python tools), parse it
                resolve(JSON.parse(textContent));
              } catch (e) {
                // Otherwise return raw text
                resolve(textContent);
              }
            } else {
              resolve(content);
            }
          }
        }
      } catch (err) {
        // Ignore incomplete lines or raw logs
      }
    }
  }
}
