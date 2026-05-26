with open('/Users/sambitacharya/Documents/projects/agents/context_builder/vscode-extension/src/extension.ts', 'r') as f:
    content = f.read()

# We need to find the duplicated part.
# The duplicated part starts with "t function activate(context: vscode.ExtensionContext) {"
duplicate_start = content.find("t function activate(context: vscode.ExtensionContext) {")

if duplicate_start != -1:
    # Inside the duplicated part, we need to find the end of the original fallback block.
    # The original fallback block ended with:
    # "in local fallback mode.\\n\\n`);\n        }"
    # Let's search for this exact string in the duplicated part to find where the original handleIngest resumed.
    
    # We will slice the content to remove everything from duplicate_start up to the end of the fallback block.
    
    original_remainder_marker = "in local fallback mode.\\n\\n`);\n        }"
    marker_idx = content.find(original_remainder_marker, duplicate_start)
    
    if marker_idx != -1:
        # The remainder of the file starts AFTER the fallback block.
        resume_idx = marker_idx + len(original_remainder_marker)
        
        # So the fixed content is:
        # content[:duplicate_start] (which is the top of file + new_agent_logic)
        # + content[resume_idx:] (which is the rest of handleIngest, query, trace, view, etc.)
        
        fixed_content = content[:duplicate_start] + content[resume_idx:]
        
        with open('/Users/sambitacharya/Documents/projects/agents/context_builder/vscode-extension/src/extension.ts', 'w') as f:
            f.write(fixed_content)
        print("Fixed extension.ts successfully!")
    else:
        print("Could not find the original remainder marker.")
else:
    print("Could not find duplicate start.")
