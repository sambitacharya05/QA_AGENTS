import re

with open('/Users/sambitacharya/Documents/projects/agents/context_builder/vscode-extension/src/extension.ts', 'r') as f:
    content = f.read()

# Add imports
imports = """import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { MCPClient } from './mcpClient';
import { CopilotClient, defineTool, approveAll } from '@github/copilot-sdk';
import { z } from 'zod';
"""
content = re.sub(r"import \* as vscode from 'vscode';\nimport \* as fs from 'fs';\nimport \* as path from 'path';\nimport \{ MCPClient \} from './mcpClient';\n", imports, content)

# Add runSilentIngestion registration
register_code = """
    const ingestCommand = vscode.commands.registerCommand('build_context.runIngestAgent', async () => {
        await runSilentIngestion(workspaceRoot);
    });
    context.subscriptions.push(ingestCommand);

    // Register the @build_context Chat Participant
"""
content = content.replace("    // Register the @build_context Chat Participant\n", register_code)

# Remove cleanJsonString since we don't need it anymore
# It spans from /** helper */ to }
clean_json_regex = re.compile(r"/\*\*\n \* Helper to clean JSON block responses from LLMs\.\n \*/\nfunction cleanJsonString\(rawText: string\): string \{\n(.*?)\n\}\n\n", re.DOTALL)
content = clean_json_regex.sub("", content)

# Now refactor handleIngest
# Find the start of targetModel
start_target_model = """        // Select native chat model (prefer Claude or Gemini)
        let targetModel: vscode.LanguageModelChat | undefined;"""

end_fallback = """        } else {
            // Safe Heuristics Fallback when vscode.lm isn't available
            stream.markdown(`⚠️ **Native Language Model API not available.** Running standard rule-based parsing and edge mapping heuristics in local fallback mode.\n\n`);
        }"""

new_agent_logic = """
        let sessionAuth;
        try {
            sessionAuth = await vscode.authentication.getSession('github', { createIfNone: true, scopes: ['copilot'] });
        } catch (e) {
            stream?.markdown(`⚠️ Failed to get GitHub Copilot token: ${e}\\n\\n`);
            return;
        }

        const client = new CopilotClient({
            gitHubToken: sessionAuth.accessToken,
            useLoggedInUser: false
        });
        await client.start();

        const addBusinessRuleTool = defineTool("add_business_rule_node", {
            description: "Records an identified business rule or product feature in the database.",
            parameters: z.object({
                id: z.string(),
                type: z.string(),
                name: z.string(),
                description: z.string()
            }),
            handler: async ({ id, type, name, description }) => {
                await mcpClient.callTool('add_node', { node_id: id, node_type: type, name, description, metadata: '{}' });
                return { success: true };
            }
        });

        const addTechComponentTool = defineTool("add_tech_component_node", {
            description: "Registers an API endpoint or data model schema in the database.",
            parameters: z.object({
                id: z.string(),
                type: z.string(),
                name: z.string(),
                description: z.string()
            }),
            handler: async ({ id, type, name, description }) => {
                await mcpClient.callTool('add_node', { node_id: id, node_type: type, name, description, metadata: '{}' });
                return { success: true };
            }
        });

        const addSemanticEdgeTool = defineTool("add_semantic_edge", {
            description: "Registers a logical connection or edge between two existing nodes.",
            parameters: z.object({
                source_id: z.string(),
                target_id: z.string(),
                relationship: z.string()
            }),
            handler: async ({ source_id, target_id, relationship }) => {
                await mcpClient.callTool('add_edge', { source_id, target_id, relationship, metadata: '{}' });
                return { success: true };
            }
        });

        // Retrieve raw documents content from the SQLite database
        const rawDocs = await mcpClient.callTool('get_raw_documents');
        const docs = JSON.parse(rawDocs);
        const documentsContext = docs.map((d: any) => `### FILE: ${d.path}\\n${d.content}\\n`).join('\\n');

        const safeStreamMarkdown = (msg: string) => {
            if (stream) stream.markdown(msg);
        };

        safeStreamMarkdown(`🤖 **Stage 2/4: Rule Extraction...** Running rule extractor subagent using **gpt-5 / gpt-4o**...\\n`);
        try {
            const ruleSession = await client.createSession({
                model: "gpt-5", // Will gracefully fallback in internal SDK if unavailable depending on version
                tools: [addBusinessRuleTool],
                onPermissionRequest: approveAll
            });
            await ruleSession.sendAndWait({ prompt: `${rulePrompt}\\n\\nExtract all insurance business rules and product features from these documents:\\n\\n${documentsContext}` });
            safeStreamMarkdown(`  ✅ Successfully executed Rule Extraction via Copilot SDK.\\n\\n`);
        } catch (err) {
            safeStreamMarkdown(`  ⚠️ Rule extraction failed: ${err}.\\n\\n`);
        }

        safeStreamMarkdown(`🤖 **Stage 3/4: Technical Component Mapping...** Running technical mapper subagent using **gpt-5 / gpt-4o**...\\n`);
        try {
            const techSession = await client.createSession({
                model: "gpt-5",
                tools: [addTechComponentTool],
                onPermissionRequest: approveAll
            });
            await techSession.sendAndWait({ prompt: `${techPrompt}\\n\\nExtract all API endpoints and data model schemas from these files:\\n\\n${documentsContext}` });
            safeStreamMarkdown(`  ✅ Successfully executed Technical Mapping via Copilot SDK.\\n\\n`);
        } catch (err) {
            safeStreamMarkdown(`  ⚠️ Technical mapping failed: ${err}.\\n\\n`);
        }

        safeStreamMarkdown(`🤖 **Stage 4/4: Relationship Linking...** Running relationship linker subagent using **gpt-5 / gpt-4o**...\\n`);
        try {
            const currentNodesRaw = await mcpClient.callTool('query_semantic_graph', { query: '' });
            const currentNodes = JSON.parse(currentNodesRaw);
            const nodesListContext = currentNodes.map((n: any) => ({
                id: n.id,
                type: n.type,
                name: n.name,
                description: n.description
            }));

            const linkSession = await client.createSession({
                model: "gpt-5",
                tools: [addSemanticEdgeTool],
                onPermissionRequest: approveAll
            });
            await linkSession.sendAndWait({ prompt: `${linkPrompt}\\n\\nEstablish all valid logical connections (edges) between these discovered entities:\\n\\n${JSON.stringify(nodesListContext, null, 2)}` });
            safeStreamMarkdown(`  ✅ Successfully executed Relationship Linking via Copilot SDK.\\n\\n`);
        } catch (err) {
            safeStreamMarkdown(`  ⚠️ Relationship linker failed: ${err}.\\n\\n`);
        }
"""

start_idx = content.find(start_target_model)
end_idx = content.find(end_fallback) + len(end_fallback)
content = content[:start_idx] + new_agent_logic + content[end_idx:]

# Since handleIngest signature has stream and token, make them optional to support runSilentIngestion
content = content.replace("async function handleIngest(workspaceRoot: string, stream: vscode.ChatResponseStream, token: vscode.CancellationToken) {", "async function handleIngest(workspaceRoot: string, stream?: vscode.ChatResponseStream, token?: vscode.CancellationToken) {")

# Modify stream calls in handleIngest to check if stream exists
content = content.replace("stream.markdown(", "if (stream) stream.markdown(")

# Add runSilentIngestion
silent_ingest_code = """
/**
 * Background hook sync trigger.
 */
async function runSilentIngestion(workspaceRoot: string) {
    try {
        await handleIngest(workspaceRoot);
    } catch (err) {
        console.error(`Silent ingestion failed: ${err}`);
    }
}
"""
content += silent_ingest_code

with open('/Users/sambitacharya/Documents/projects/agents/context_builder/vscode-extension/src/extension.ts', 'w') as f:
    f.write(content)

